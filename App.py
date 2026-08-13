"""
Sistema de Ocorrências - arquivo único.

Rodar local:  streamlit run app.py
Publicar:     GitHub + share.streamlit.io

Estrutura do repositório:
    app.py
    requirements.txt
    dados/produtos.csv           (semente inicial das bases)
    dados/pessoas.json
    dados/motivos.csv
    dados/carregamentos.csv
    dados/historico_2026.csv

As bases passam a viver na planilha do Google Sheets, em abas próprias
("base produtos", "base pessoas", "base motivos", "base carregamentos").
Os arquivos em dados/ servem só como semente na primeira carga.
"""
import hashlib
import io
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# ==========================================================================
# CONFIGURAÇÃO
# ==========================================================================
BASE_DIR = Path(__file__).parent
DADOS = BASE_DIR / "dados"


def _localizar(nome: str) -> Path:
    """Procura o arquivo em dados/ e, se não achar, na raiz do projeto."""
    for caminho in (DADOS / nome, BASE_DIR / nome):
        if caminho.exists():
            return caminho
    return DADOS / nome


ARQ_PRODUTOS = _localizar("produtos.csv")
ARQ_MOTIVOS = _localizar("motivos.csv")
ARQ_PESSOAS = _localizar("pessoas.json")
ARQ_CARREGAMENTOS = _localizar("carregamentos.csv")
ARQ_HISTORICO = _localizar("historico_2026.csv")
ARQ_OCORRENCIAS = DADOS / "ocorrencias.csv"  # só no modo CSV local

# Abas da planilha do Google Sheets
ABA_OCORRENCIAS = "ocorrencias"
ABA_PRODUTOS = "base produtos"
ABA_PESSOAS = "pessoas"
ABA_MOTIVOS = "base motivos"
ABA_CARREGAMENTOS = "base carregamentos"

# Nomes alternativos aceitos na leitura (o primeiro que existir é usado)
ALTERNATIVAS = {
    ABA_PRODUTOS: ["base produtos", "produtos"],
    ABA_PESSOAS: ["pessoas", "base pessoas"],
    ABA_MOTIVOS: ["base motivos", "motivos"],
    ABA_CARREGAMENTOS: ["base carregamentos", "carregamentos"],
}

COLS_PRODUTOS = ["CODPROD", "DESCRICAO", "EMBALAGEMMASTER", "QTUNITCX", "CUSTO"]
COLS_PESSOAS = ["CONFERENTE", "SEPARADOR", "ENTREGADOR"]
COLS_MOTIVOS = ["MOTIVO", "SETOR"]
COLS_CARREGAMENTOS = ["nota_fiscal", "carregamento", "placa", "entregador"]

TIPOS_PESSOA = {"conferentes": "CONFERENTE", "separadores": "SEPARADOR",
                "entregadores": "ENTREGADOR"}

MESES_PT = {1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN",
            7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ"}

COLUNAS = [
    "id", "criado_em", "mes", "data", "entregador", "placa", "carregamento",
    "nota_fiscal", "codigo", "produto", "embalagem", "quantidade", "custo_un",
    "valor_financeiro", "motivo", "setor", "responsavel_registro",
    "responsavel_analise", "separador", "conferente", "erro_separador",
    "bipado", "obs",
]

ROTULOS = {
    "id": "ID", "criado_em": "Criado em", "mes": "Mês/Ano", "data": "Data",
    "entregador": "Entregador", "placa": "Placa", "carregamento": "Carregamento",
    "nota_fiscal": "Nota Fiscal", "codigo": "Código", "produto": "Produto",
    "embalagem": "Embalagem", "quantidade": "Qtd", "custo_un": "Custo Un.",
    "valor_financeiro": "Valor Financeiro", "motivo": "Motivo", "setor": "Setor",
    "responsavel_registro": "Responsável pelo Registro",
    "responsavel_analise": "Responsável / Análise", "separador": "Separador",
    "conferente": "Conferente", "erro_separador": "Erro do Separador",
    "bipado": "Bipado", "obs": "OBS",
}

EMBALAGENS_LIVRES = ["UNIDADE", "CAIXA", "FARDO", "SACO", "PACOTE", "BANDEJA",
                     "VOLUME"]
SETORES = ["M&A", "PCE", "ENTREGA", "EMBARQUE", "FATURAMENTO"]


# ==========================================================================
# AUTENTICAÇÃO
# ==========================================================================
def gerar_hash(senha: str) -> str:
    return hashlib.sha256(senha.encode("utf-8")).hexdigest()


def _usuarios() -> dict:
    try:
        return dict(st.secrets["usuarios"])
    except Exception:
        return {"admin": {"nome": "ADMINISTRADOR",
                          "senha_hash": gerar_hash("admin123"),
                          "perfil": "admin"}}


def tela_login():
    if "usuario" in st.session_state:
        return st.session_state["usuario"]

    _, meio, _ = st.columns([1, 1.4, 1])
    with meio:
        st.markdown("### 📦 Sistema de Ocorrências")
        st.caption("Faça login para registrar e acompanhar as ocorrências.")
        with st.form("login"):
            user = st.text_input("Usuário").strip().lower()
            senha = st.text_input("Senha", type="password")
            entrar = st.form_submit_button("Entrar", use_container_width=True,
                                           type="primary")
        if entrar:
            dados = _usuarios().get(user)
            if dados and gerar_hash(senha) == dict(dados).get("senha_hash"):
                d = dict(dados)
                st.session_state["usuario"] = {
                    "login": user,
                    "nome": d.get("nome", user.upper()),
                    "perfil": d.get("perfil", "registro"),
                    "cargo": d.get("cargo", ""),
                }
                st.rerun()
            else:
                st.error("Usuário ou senha inválidos.")

        with st.expander("Gerar hash de senha"):
            s = st.text_input("Senha para gerar o hash", key="gh")
            if s:
                st.code(gerar_hash(s), language=None)
                st.caption("Cole este valor em senha_hash, no secrets do app.")
    return None


def pode_editar(u: dict) -> bool:
    """Analistas, encarregados e gestores."""
    return u.get("perfil") in ("analista", "admin")


def eh_admin(u: dict) -> bool:
    return u.get("perfil") == "admin"


# ==========================================================================
# CONEXÃO COM O GOOGLE SHEETS
# ==========================================================================
def usando_sheets() -> bool:
    try:
        return "gcp_service_account" in st.secrets and "planilha" in st.secrets
    except Exception:
        return False


def nome_backend() -> str:
    return "Google Sheets" if usando_sheets() else "CSV local"


@st.cache_resource(show_spinner=False)
def _planilha():
    import gspread
    from google.oauth2.service_account import Credentials

    escopos = ["https://www.googleapis.com/auth/spreadsheets",
               "https://www.googleapis.com/auth/drive"]
    cred = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]), scopes=escopos)
    return gspread.authorize(cred).open_by_key(st.secrets["planilha"]["id"])


def _aba(nome: str, criar=False, colunas=None):
    """Devolve a worksheet pelo nome, ignorando maiúsculas/minúsculas.

    Cria a aba se criar=True e ela não existir. None caso contrário.
    """
    planilha = _planilha()
    alvo = nome.strip().lower()
    for ws in planilha.worksheets():
        if ws.title.strip().lower() == alvo:
            return ws
    if not criar:
        return None
    ws = planilha.add_worksheet(nome, rows=1000, cols=max(5, len(colunas or [])))
    if colunas:
        ws.update(values=[colunas], range_name="A1")
    return ws


@st.cache_data(ttl=1800, show_spinner=False)
def ler_aba(nome: str) -> pd.DataFrame:
    """Lê uma aba como DataFrame de strings, aceitando nomes alternativos."""
    if not usando_sheets():
        return pd.DataFrame()
    ws = None
    for candidata in ALTERNATIVAS.get(nome, [nome]):
        ws = _aba(candidata)
        if ws is not None:
            break
    if ws is None:
        return pd.DataFrame()
    valores = ws.get_all_values()
    if len(valores) < 2:
        return pd.DataFrame()

    cabecalho = valores[0]
    largura = max(len(l) for l in valores)
    cabecalho += [""] * (largura - len(cabecalho))

    # Colunas em branco (separadoras) e nomes repetidos ganham rótulo próprio,
    # para o DataFrame não quebrar.
    nomes, vistos = [], {}
    for i, c in enumerate(cabecalho):
        nome_col = str(c).strip() or f"_vazia_{i}"
        vistos[nome_col] = vistos.get(nome_col, 0) + 1
        nomes.append(nome_col if vistos[nome_col] == 1
                     else f"{nome_col}_{vistos[nome_col]}")

    linhas = [l + [""] * (largura - len(l)) for l in valores[1:]]
    return pd.DataFrame(linhas, columns=nomes)


def salvar_aba(nome: str, df: pd.DataFrame, colunas: list) -> int:
    """Grava o DataFrame inteiro na aba, em lotes. Devolve o nº de linhas."""
    df = df[colunas].fillna("").astype(str)
    linhas = [colunas] + df.values.tolist()

    ws = _aba(nome, criar=True, colunas=colunas)
    ws.clear()
    ws.resize(rows=max(len(linhas) + 50, 100), cols=len(colunas))

    LOTE = 5000
    barra = st.progress(0.0, text="Enviando para a planilha...")
    for inicio in range(0, len(linhas), LOTE):
        pedaco = linhas[inicio:inicio + LOTE]
        ws.update(values=pedaco, range_name=f"A{inicio + 1}")
        barra.progress(min(1.0, (inicio + LOTE) / len(linhas)),
                       text=f"Enviando {min(inicio + LOTE, len(linhas)):,} "
                            f"de {len(linhas):,} linhas...".replace(",", "."))
    barra.empty()
    ler_aba.clear()
    limpar_cache_bases()
    return len(df)


# ==========================================================================
# BASES DE APOIO (Sheets com fallback nos arquivos do repositório)
# ==========================================================================
@st.cache_data(ttl=1800, show_spinner=False)
def base_produtos() -> pd.DataFrame:
    df = ler_aba(ABA_PRODUTOS)
    origem = "planilha"
    if df.empty and ARQ_PRODUTOS.exists():
        df = pd.read_csv(ARQ_PRODUTOS, dtype=str)
        origem = "repositório"
    if df.empty:
        return pd.DataFrame(columns=COLS_PRODUTOS + ["_origem"])
    for c in COLS_PRODUTOS:
        if c not in df.columns:
            df[c] = ""
    df = df[COLS_PRODUTOS].copy()
    df["CODPROD"] = df["CODPROD"].astype(str).str.strip()
    df["DESCRICAO"] = df["DESCRICAO"].fillna("").astype(str).str.strip()
    for c in ("QTUNITCX", "CUSTO"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["_origem"] = origem
    return df


@st.cache_data(ttl=1800, show_spinner=False)
def base_motivos() -> pd.DataFrame:
    df = ler_aba(ABA_MOTIVOS)
    if df.empty and ARQ_MOTIVOS.exists():
        df = pd.read_csv(ARQ_MOTIVOS)
    if df.empty:
        return pd.DataFrame(columns=COLS_MOTIVOS)
    return df.reindex(columns=COLS_MOTIVOS).dropna(subset=["MOTIVO"])


@st.cache_data(ttl=1800, show_spinner=False)
def base_pessoas() -> dict:
    """Lê a aba de pessoas em qualquer um dos dois formatos:

    A) uma coluna por tipo: CONFERENTE | SEPARADOR | ENTREGADOR
    B) duas colunas em lista: TIPO | NOME
    """
    df = ler_aba(ABA_PESSOAS)
    if not df.empty:
        colunas = {str(c).strip().upper(): c for c in df.columns}

        # Formato B: TIPO / NOME
        if {"TIPO", "NOME"} <= set(colunas):
            tipos = df[colunas["TIPO"]].astype(str).str.strip().str.upper()
            return {chave: sorted({str(n).strip().upper()
                                   for n in df.loc[tipos == tipo, colunas["NOME"]]
                                   if str(n).strip()})
                    for chave, tipo in TIPOS_PESSOA.items()}

        # Formato A: uma coluna por tipo (colunas em branco entre elas são
        # ignoradas, pois recebem rótulo _vazia_N na leitura)
        resultado = {}
        for chave, tipo in TIPOS_PESSOA.items():
            achada = next((orig for nome, orig in colunas.items()
                           if nome.startswith(tipo)), None)
            resultado[chave] = sorted({str(n).strip().upper()
                                       for n in df[achada]
                                       if str(n).strip()}) if achada else []
        if any(resultado.values()):
            return resultado

    if ARQ_PESSOAS.exists():
        return json.loads(ARQ_PESSOAS.read_text(encoding="utf-8"))
    return {k: [] for k in TIPOS_PESSOA}


@st.cache_data(ttl=1800, show_spinner=False)
def base_carregamentos() -> pd.DataFrame:
    df = ler_aba(ABA_CARREGAMENTOS)
    if df.empty and ARQ_CARREGAMENTOS.exists():
        df = pd.read_csv(ARQ_CARREGAMENTOS, dtype=str)
    if df.empty:
        return pd.DataFrame(columns=COLS_CARREGAMENTOS)
    df = df.reindex(columns=COLS_CARREGAMENTOS).fillna("")
    df["nota_fiscal"] = df["nota_fiscal"].astype(str).str.strip()
    return df.drop_duplicates("nota_fiscal", keep="last")


@st.cache_data(show_spinner=False)
def base_historico() -> pd.DataFrame:
    if not ARQ_HISTORICO.exists():
        return pd.DataFrame()
    df = pd.read_csv(ARQ_HISTORICO, dtype=str)
    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    for c in ("quantidade", "custo_un"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["valor_financeiro"] = df["quantidade"] * df["custo_un"]
    mot = base_motivos()
    df["setor"] = df["motivo"].map(dict(zip(mot["MOTIVO"], mot["SETOR"]))).fillna("")
    return df


def limpar_cache_bases():
    for f in (base_produtos, base_motivos, base_pessoas, base_carregamentos,
              base_historico):
        f.clear()


# -------------------------------------------------------- auto-preenchimento
def buscar_nf(nota_fiscal: str) -> dict:
    nf = str(nota_fiscal).strip()
    if not nf:
        return {}
    base = base_carregamentos()
    achado = base[base["nota_fiscal"] == nf]
    if achado.empty:
        return {}
    l = achado.iloc[0]
    return {"carregamento": str(l.get("carregamento", "") or ""),
            "placa": str(l.get("placa", "") or "").upper(),
            "entregador": str(l.get("entregador", "") or "").upper()}


def buscar_produto(codigo: str) -> dict:
    cod = str(codigo).strip()
    if not cod:
        return {}
    df = base_produtos()
    achado = df[df["CODPROD"] == cod]
    if achado.empty:
        return {}
    l = achado.iloc[0]
    custo = l.get("CUSTO")
    return {"produto": str(l["DESCRICAO"]),
            "embalagem": str(l.get("EMBALAGEMMASTER", "") or "UNIDADE"),
            "qt_unit_cx": float(l["QTUNITCX"]) if pd.notna(l.get("QTUNITCX")) else 1.0,
            "custo_un": float(custo) if pd.notna(custo) else None}


def setor_do_motivo(motivo: str) -> str:
    mot = base_motivos()
    return dict(zip(mot["MOTIVO"], mot["SETOR"])).get(motivo, "")


def mes_ano(d) -> str:
    return f"{MESES_PT[d.month]}/{str(d.year)[2:]}"


# ==========================================================================
# OCORRÊNCIAS
# ==========================================================================
def _aba_ocorrencias():
    nome = st.secrets["planilha"].get("aba", ABA_OCORRENCIAS)
    ws = _aba(nome, criar=True, colunas=COLUNAS)
    if not ws.row_values(1):
        ws.update(values=[COLUNAS], range_name="A1")
    return ws


@st.cache_data(ttl=60, show_spinner=False)
def carregar_ocorrencias() -> pd.DataFrame:
    if usando_sheets():
        df = pd.DataFrame(_aba_ocorrencias().get_all_records())
    elif ARQ_OCORRENCIAS.exists():
        df = pd.read_csv(ARQ_OCORRENCIAS, dtype=str)
    else:
        df = pd.DataFrame(columns=COLUNAS)

    for c in COLUNAS:
        if c not in df.columns:
            df[c] = ""
    df = df[COLUNAS]
    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    for c in ("quantidade", "custo_un", "valor_financeiro"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def gravar_ocorrencia(registro: dict) -> str:
    df = carregar_ocorrencias()
    novos = [i for i in df["id"].astype(str) if i.startswith("OC")]
    registro = dict(registro)
    registro["id"] = f"OC{max([int(i[2:]) for i in novos], default=0) + 1:05d}"
    registro["criado_em"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linha = [str(registro.get(c, "") or "") for c in COLUNAS]

    if usando_sheets():
        _aba_ocorrencias().append_row(linha, value_input_option="USER_ENTERED")
    else:
        ARQ_OCORRENCIAS.parent.mkdir(parents=True, exist_ok=True)
        existe = ARQ_OCORRENCIAS.exists()
        pd.DataFrame([registro])[COLUNAS].to_csv(
            ARQ_OCORRENCIAS, mode="a" if existe else "w", header=not existe,
            index=False)

    carregar_ocorrencias.clear()
    return registro["id"]


def atualizar_ocorrencia(id_oc: str, campos: dict) -> bool:
    if usando_sheets():
        ws = _aba_ocorrencias()
        ids = ws.col_values(COLUNAS.index("id") + 1)
        if id_oc not in ids:
            return False
        linha = ids.index(id_oc) + 1
        for campo, valor in campos.items():
            if campo in COLUNAS:
                ws.update_cell(linha, COLUNAS.index(campo) + 1, str(valor or ""))
    else:
        if not ARQ_OCORRENCIAS.exists():
            return False
        df = pd.read_csv(ARQ_OCORRENCIAS, dtype=str)
        alvo = df["id"] == id_oc
        if not alvo.any():
            return False
        for campo, valor in campos.items():
            if campo in df.columns:
                df.loc[alvo, campo] = str(valor or "")
        df.to_csv(ARQ_OCORRENCIAS, index=False)

    carregar_ocorrencias.clear()
    return True


def dados_completos(incluir_historico: bool) -> pd.DataFrame:
    df = carregar_ocorrencias()
    if incluir_historico:
        hist = base_historico()
        if not hist.empty:
            df = pd.concat([hist.reindex(columns=COLUNAS, fill_value=""), df],
                           ignore_index=True)
    return df


# ==========================================================================
# PÁGINA: REGISTRAR
# ==========================================================================
def _cb_nf():
    st.session_state["nf_dados"] = buscar_nf(st.session_state.get("nf", ""))


def _cb_produto():
    st.session_state["prod_dados"] = buscar_produto(st.session_state.get("cod", ""))


def pagina_registrar(usuario: dict):
    st.subheader("Registrar ocorrência")
    pes = base_pessoas()
    lista_motivos = sorted(base_motivos()["MOTIVO"].tolist())

    c1, c2 = st.columns([1, 2])
    c1.text_input("Nota Fiscal", key="nf", on_change=_cb_nf,
                  placeholder="Digite e pressione Enter")
    nf_dados = st.session_state.get("nf_dados", {})
    with c2:
        if st.session_state.get("nf") and nf_dados:
            st.success(f"Carregamento **{nf_dados['carregamento']}** · "
                       f"Placa **{nf_dados['placa']}** · "
                       f"Entregador **{nf_dados['entregador']}**")
        elif st.session_state.get("nf"):
            st.warning("NF não encontrada na base de carregamentos. "
                       "Preencha os campos abaixo manualmente.")

    c1, c2, c3 = st.columns(3)
    carregamento = c1.text_input("Carregamento", value=nf_dados.get("carregamento", ""))
    placa = c2.text_input("Placa", value=nf_dados.get("placa", "")).upper()

    ent_auto = nf_dados.get("entregador", "")
    opcoes_ent = list(pes["entregadores"])
    if ent_auto and ent_auto not in opcoes_ent:
        opcoes_ent.insert(0, ent_auto)
    entregador = c3.selectbox("Entregador", opcoes_ent or [""],
                              index=opcoes_ent.index(ent_auto)
                              if ent_auto in opcoes_ent else 0)
    st.divider()

    c1, c2 = st.columns([1, 3])
    c1.text_input("Código do produto", key="cod", on_change=_cb_produto,
                  placeholder="Digite e pressione Enter")
    prod = st.session_state.get("prod_dados", {})
    descricao = c2.text_input("Descrição do produto", value=prod.get("produto", ""))

    c1, c2, c3, c4 = st.columns(4)
    emb_auto = prod.get("embalagem", "")
    opcoes_emb = EMBALAGENS_LIVRES.copy()
    if emb_auto and emb_auto not in opcoes_emb:
        opcoes_emb.insert(0, emb_auto)
    embalagem = c1.selectbox("Embalagem", opcoes_emb)
    quantidade = c2.number_input("Quantidade", min_value=0.0, step=1.0, value=1.0)
    custo_un = c3.number_input("Custo unitário (R$)", min_value=0.0, step=0.01,
                               value=float(prod.get("custo_un") or 0.0),
                               format="%.4f")

    qt_cx = prod.get("qt_unit_cx", 1.0) or 1.0
    fator = qt_cx if (embalagem == emb_auto and embalagem != "UNIDADE") else 1.0
    valor = quantidade * fator * custo_un
    c4.metric("Valor financeiro", f"R$ {valor:,.2f}")
    st.divider()

    c1, c2 = st.columns([2, 1])
    motivo = c1.selectbox("Motivo", lista_motivos)
    setor = setor_do_motivo(motivo)
    c2.text_input("Setor (automático)", value=setor, disabled=True)

    c1, c2, c3 = st.columns(3)
    separador = c1.selectbox("Separador", [""] + list(pes["separadores"]))
    conferente = c2.selectbox("Conferente", [""] + list(pes["conferentes"]))
    responsavel_analise = c3.text_input("Responsável pela análise")

    c1, c2 = st.columns(2)
    erro_separador = c1.radio("Erro do separador?", ["", "SIM", "NÃO"],
                              horizontal=True)
    bipado = c2.radio("Bipado?", ["", "SIM", "NÃO"], horizontal=True)

    data_oc = st.date_input("Data da ocorrência", value=date.today())
    obs = st.text_area("Observações", height=80)
    st.caption(f"Registrado por **{usuario['nome']}** · "
               f"Mês/Ano: **{mes_ano(data_oc)}**")

    if st.button("Registrar ocorrência", type="primary", use_container_width=True):
        erros = []
        if not st.session_state.get("nf", "").strip():
            erros.append("Nota fiscal")
        if not st.session_state.get("cod", "").strip():
            erros.append("Código do produto")
        if quantidade <= 0:
            erros.append("Quantidade")
        if erros:
            st.error("Preencha: " + ", ".join(erros))
            return

        novo_id = gravar_ocorrencia({
            "mes": mes_ano(data_oc),
            "data": data_oc.strftime("%Y-%m-%d"),
            "entregador": entregador, "placa": placa,
            "carregamento": carregamento,
            "nota_fiscal": st.session_state["nf"].strip(),
            "codigo": st.session_state["cod"].strip(),
            "produto": descricao.upper(), "embalagem": embalagem,
            "quantidade": quantidade, "custo_un": round(custo_un, 4),
            "valor_financeiro": round(valor, 2), "motivo": motivo, "setor": setor,
            "responsavel_registro": usuario["nome"],
            "responsavel_analise": responsavel_analise.upper(),
            "separador": separador, "conferente": conferente,
            "erro_separador": erro_separador, "bipado": bipado,
            "obs": obs.strip(),
        })
        st.success(f"Ocorrência {novo_id} registrada com sucesso.")
        for k in ("nf", "cod", "nf_dados", "prod_dados"):
            st.session_state.pop(k, None)


# ==========================================================================
# FILTROS E EXPORTAÇÃO
# ==========================================================================
def aplicar_filtros(df: pd.DataFrame, chave: str) -> pd.DataFrame:
    if df.empty:
        return df
    hoje = date.today()
    c1, c2, c3 = st.columns([1, 1, 2])
    inicio = c1.date_input("De", value=hoje - timedelta(days=30), key=f"de_{chave}")
    fim = c2.date_input("Até", value=hoje, key=f"ate_{chave}")

    with c3.expander("Mais filtros"):
        f1, f2 = st.columns(2)
        setores = f1.multiselect("Setor", sorted(df["setor"].dropna().unique()),
                                 key=f"set_{chave}")
        mot = f2.multiselect("Motivo", sorted(df["motivo"].dropna().unique()),
                             key=f"mot_{chave}")
        f3, f4 = st.columns(2)
        ent = f3.multiselect("Entregador",
                             sorted(df["entregador"].dropna().unique()),
                             key=f"ent_{chave}")
        sep = f4.multiselect("Separador",
                             sorted(df["separador"].dropna().unique()),
                             key=f"sep_{chave}")

    m = df["data"].notna()
    m &= df["data"].dt.date >= inicio
    m &= df["data"].dt.date <= fim
    if setores:
        m &= df["setor"].isin(setores)
    if mot:
        m &= df["motivo"].isin(mot)
    if ent:
        m &= df["entregador"].isin(ent)
    if sep:
        m &= df["separador"].isin(sep)
    return df[m]


def para_excel(df: pd.DataFrame) -> bytes:
    saida = io.BytesIO()
    export = df.copy()
    export["data"] = export["data"].dt.strftime("%d/%m/%Y")
    export = export.rename(columns=ROTULOS)
    with pd.ExcelWriter(saida, engine="openpyxl") as w:
        export.to_excel(w, sheet_name="Ocorrências", index=False)
        if not df.empty:
            (df.groupby(["setor", "motivo"])
               .agg(Ocorrências=("id", "count"),
                    Valor=("valor_financeiro", "sum"))
               .reset_index()
               .to_excel(w, sheet_name="Resumo", index=False))
    return saida.getvalue()


# ==========================================================================
# PÁGINA: CONSULTAR
# ==========================================================================
def pagina_consultar(usuario: dict):
    st.subheader("Consultar ocorrências")
    incluir = st.toggle("Incluir histórico importado da planilha", value=True)
    df = dados_completos(incluir)
    if df.empty:
        st.info("Nenhuma ocorrência registrada ainda.")
        return

    filtrado = aplicar_filtros(df, "consulta")
    c1, c2, c3 = st.columns(3)
    c1.metric("Ocorrências", len(filtrado))
    c2.metric("Valor financeiro", f"R$ {filtrado['valor_financeiro'].sum():,.2f}")
    c3.metric("Qtd. de itens", f"{filtrado['quantidade'].sum():,.0f}")

    visiveis = ["id", "data", "nota_fiscal", "codigo", "produto", "embalagem",
                "quantidade", "motivo", "setor", "entregador", "separador",
                "conferente", "responsavel_registro", "valor_financeiro", "obs"]
    st.dataframe(
        filtrado[visiveis].rename(columns=ROTULOS),
        use_container_width=True, hide_index=True,
        column_config={
            "Data": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Valor Financeiro": st.column_config.NumberColumn(format="R$ %.2f"),
        },
    )

    c1, c2 = st.columns(2)
    c1.download_button("Baixar planilha (.xlsx)", para_excel(filtrado),
                       file_name=f"ocorrencias_{date.today():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet",
                       use_container_width=True)
    c2.download_button("Baixar CSV",
                       filtrado.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"ocorrencias_{date.today():%Y%m%d}.csv",
                       mime="text/csv", use_container_width=True)

    if not pode_editar(usuario):
        return

    st.divider()
    st.markdown("##### Analisar / editar ocorrência")
    editaveis = filtrado[filtrado["id"].astype(str).str.startswith("OC")]
    if editaveis.empty:
        st.caption("Nenhuma ocorrência editável no filtro atual.")
        return
    alvo = st.selectbox(
        "Ocorrência", editaveis["id"].tolist(),
        format_func=lambda i: f"{i} · "
        + str(editaveis.loc[editaveis['id'] == i, 'produto'].iloc[0])[:40])
    linha = editaveis[editaveis["id"] == alvo].iloc[0]
    with st.form("editar"):
        c1, c2 = st.columns(2)
        resp = c1.text_input("Responsável pela análise",
                             value=str(linha["responsavel_analise"] or ""))
        opcoes = ["", "SIM", "NÃO"]
        atual = str(linha["erro_separador"] or "")
        erro_sep = c2.radio("Erro do separador?", opcoes, horizontal=True,
                            index=opcoes.index(atual) if atual in opcoes else 0)
        obs = st.text_area("Observações", value=str(linha["obs"] or ""))
        if st.form_submit_button("Salvar alterações", type="primary"):
            atualizar_ocorrencia(alvo, {"responsavel_analise": resp.upper(),
                                        "erro_separador": erro_sep, "obs": obs})
            st.success("Ocorrência atualizada.")
            st.rerun()


# ==========================================================================
# PÁGINA: PAINEL
# ==========================================================================
def _grafico_barra(df, campo, titulo, top=10):
    dados = (df.groupby(campo)
               .agg(qtd=("id", "count"), valor=("valor_financeiro", "sum"))
               .reset_index().sort_values("qtd", ascending=False).head(top))
    dados = dados[dados[campo].astype(str).str.strip() != ""]
    if dados.empty:
        st.caption(f"Sem dados para {titulo}.")
        return
    st.altair_chart(
        alt.Chart(dados).mark_bar(cornerRadiusEnd=4).encode(
            x=alt.X("qtd:Q", title="Ocorrências"),
            y=alt.Y(f"{campo}:N", sort="-x", title=None),
            tooltip=[alt.Tooltip(f"{campo}:N", title=titulo),
                     alt.Tooltip("qtd:Q", title="Ocorrências"),
                     alt.Tooltip("valor:Q", title="Valor (R$)", format=",.2f")],
            color=alt.Color("qtd:Q", scale=alt.Scale(scheme="blues"), legend=None),
        ).properties(height=max(180, 28 * len(dados))),
        use_container_width=True)


def pagina_painel(usuario: dict):
    st.subheader("Painel de acompanhamento")
    incluir = st.toggle("Incluir histórico importado da planilha", value=True,
                        key="hist_dash")
    df = dados_completos(incluir)
    if df.empty:
        st.info("Nenhuma ocorrência registrada ainda.")
        return

    df = aplicar_filtros(df, "dash")
    if df.empty:
        st.warning("Nenhuma ocorrência no período selecionado.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ocorrências", len(df))
    c2.metric("Valor financeiro", f"R$ {df['valor_financeiro'].sum():,.2f}")
    c3.metric("Notas fiscais", df["nota_fiscal"].nunique())
    c4.metric("Motivo mais comum", df["motivo"].mode().iat[0] if len(df) else "-")

    st.markdown("##### Evolução diária")
    serie = df.groupby(df["data"].dt.date).size().reset_index(name="qtd")
    serie.columns = ["data", "qtd"]
    st.altair_chart(
        alt.Chart(serie).mark_area(line={"color": "#1f77b4"}, opacity=0.3,
                                   interpolate="monotone").encode(
            x=alt.X("data:T", title=None),
            y=alt.Y("qtd:Q", title="Ocorrências"),
            tooltip=["data:T", "qtd:Q"]).properties(height=220),
        use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### Por motivo")
        _grafico_barra(df, "motivo", "Motivo")
        st.markdown("##### Por separador")
        _grafico_barra(df, "separador", "Separador")
    with c2:
        st.markdown("##### Por setor")
        _grafico_barra(df, "setor", "Setor", top=8)
        st.markdown("##### Por entregador")
        _grafico_barra(df, "entregador", "Entregador")

    st.markdown("##### Por conferente")
    _grafico_barra(df, "conferente", "Conferente")
    st.markdown("##### Top 10 produtos com ocorrência")
    _grafico_barra(df, "produto", "Produto")

    with st.expander("Ver tabela mensal"):
        st.dataframe(
            df.groupby([df["data"].dt.to_period("M").astype(str), "setor"])
              .size().unstack(fill_value=0),
            use_container_width=True)


# ==========================================================================
# PÁGINA: BASES
# ==========================================================================
def _ler_upload(arq, colunas_maiusculas=False) -> pd.DataFrame:
    df = (pd.read_csv(arq, dtype=str) if arq.name.lower().endswith(".csv")
          else pd.read_excel(arq, dtype=str))
    df.columns = [str(c).strip().upper() if colunas_maiusculas
                  else str(c).strip().lower().replace(" ", "_")
                  for c in df.columns]
    return df.fillna("")


def _exigir_sheets() -> bool:
    if usando_sheets():
        return True
    st.error("O Google Sheets não está configurado. Sem ele, a importação vale "
             "só até o próximo reinício do app e os dados se perdem. Configure "
             "os blocos [planilha] e [gcp_service_account] no secrets.")
    return False


def _importar(titulo: str, aba: str, colunas: list, chave: str,
              renomear: dict = None, maiusculas=False, chave_unica: str = None):
    """Bloco padrão de importação: upload -> prévia -> gravação na planilha."""
    arq = st.file_uploader(f"{titulo} (.csv ou .xlsx)", type=["csv", "xlsx"],
                           key=f"up_{chave}")
    if not arq:
        return

    novo = _ler_upload(arq, maiusculas)
    if renomear:
        novo = novo.rename(columns=renomear)

    faltando = [c for c in colunas if c not in novo.columns]
    if faltando:
        st.error(f"Colunas faltando no arquivo: {', '.join(faltando)}")
        st.caption(f"Colunas encontradas: {', '.join(novo.columns)}")
        return

    novo = novo[colunas]
    if chave_unica:
        novo = novo.drop_duplicates(chave_unica, keep="last")

    st.success(f"Arquivo lido: {len(novo):,} linhas.".replace(",", "."))
    st.dataframe(novo.head(10), use_container_width=True, hide_index=True)

    modo = st.radio("Modo", ["Substituir a base inteira",
                             "Acrescentar ao que já existe"],
                    horizontal=True, key=f"modo_{chave}")

    if st.button(f"Importar e salvar na aba \"{aba}\"", type="primary",
                 key=f"btn_{chave}"):
        if not _exigir_sheets():
            return
        final = novo
        if modo.startswith("Acrescentar"):
            atual = ler_aba(aba)
            if not atual.empty:
                final = pd.concat([atual.reindex(columns=colunas), novo],
                                  ignore_index=True)
                if chave_unica:
                    final = final.drop_duplicates(chave_unica, keep="last")
        total = salvar_aba(aba, final, colunas)
        st.success(f"Base salva na planilha: {total:,} linhas na aba "
                   f"\"{aba}\".".replace(",", "."))
        st.rerun()


def pagina_bases(usuario: dict):
    st.subheader("Bases do sistema")

    if usando_sheets():
        st.info("As bases ficam salvas na planilha do Google Sheets, em abas "
                "próprias. O que você importar aqui permanece após reinícios "
                "do app.")
    else:
        st.warning("Google Sheets não configurado — as bases estão sendo lidas "
                   "dos arquivos do repositório e a importação não é "
                   "permanente.", icon="⚠️")

    ab1, ab2, ab3, ab4 = st.tabs(["Produtos", "Carregamentos (NF)", "Pessoas",
                                  "Motivos"])

    # ------------------------------------------------------------ Produtos
    with ab1:
        prod = base_produtos()
        c1, c2 = st.columns(2)
        c1.metric("Produtos na base", f"{len(prod):,}".replace(",", "."))
        origem = prod["_origem"].iat[0] if len(prod) else "-"
        c2.metric("Origem atual", f"aba \"{ABA_PRODUTOS}\""
                  if origem == "planilha" else "arquivo do repositório")

        busca = st.text_input("Buscar por código ou descrição")
        if busca:
            m = (prod["CODPROD"].str.contains(busca, case=False, na=False)
                 | prod["DESCRICAO"].str.contains(busca, case=False, na=False))
            st.dataframe(prod[m].head(50).drop(columns=["_origem"]),
                         use_container_width=True, hide_index=True)

        st.divider()
        st.markdown("##### Importar base de produtos")
        st.caption("Colunas aceitas: CODPROD, DESCRICAO, EMBALAGEMMASTER, "
                   "QTUNITCX, CUSTO (ou CUSTOREAL). Base grande demora "
                   "alguns minutos para subir.")
        _importar("Arquivo de produtos", ABA_PRODUTOS, COLS_PRODUTOS, "prod",
                  renomear={"CUSTOREAL": "CUSTO", "CUSTOULTENT": "CUSTO"},
                  maiusculas=True, chave_unica="CODPROD")

    # ------------------------------------------------------ Carregamentos
    with ab2:
        atual = base_carregamentos()
        st.metric("Notas na base", f"{len(atual):,}".replace(",", "."))
        st.caption("Base que preenche entregador, placa e carregamento a "
                   "partir da nota fiscal. Colunas: nota_fiscal, carregamento, "
                   "placa, entregador.")
        _importar("Arquivo de carregamentos", ABA_CARREGAMENTOS,
                  COLS_CARREGAMENTOS, "carreg",
                  renomear={"nf": "nota_fiscal", "nota": "nota_fiscal",
                            "motorista": "entregador"},
                  chave_unica="nota_fiscal")
        st.dataframe(atual.tail(15), use_container_width=True, hide_index=True)

    # ------------------------------------------------------------- Pessoas
    with ab3:
        pes = base_pessoas()
        st.caption(f"Lendo da aba \"{ABA_PESSOAS}\" da planilha. Aceita dois "
                   "formatos: uma coluna por tipo (CONFERENTE, SEPARADOR, "
                   "ENTREGADOR) ou duas colunas (TIPO, NOME). Edite abaixo e "
                   "salve, ou importe um arquivo.")
        cols = st.columns(3)
        novos = {}
        for col, chave, titulo in zip(cols, TIPOS_PESSOA,
                                      ["Conferentes", "Separadores",
                                       "Entregadores"]):
            with col:
                st.markdown(f"**{titulo}** ({len(pes[chave])})")
                texto = st.text_area(titulo, "\n".join(pes[chave]), height=280,
                                     label_visibility="collapsed",
                                     key=f"ta_{chave}")
                novos[chave] = [l.strip().upper() for l in texto.splitlines()
                                if l.strip()]

        if st.button("Salvar pessoas na planilha", type="primary"):
            if _exigir_sheets():
                maior = max((len(v) for v in novos.values()), default=0)
                tabela = pd.DataFrame({
                    TIPOS_PESSOA[k]: novos[k] + [""] * (maior - len(novos[k]))
                    for k in TIPOS_PESSOA})
                total = salvar_aba(ABA_PESSOAS, tabela, COLS_PESSOAS)
                st.success(f"Listas salvas na aba \"{ABA_PESSOAS}\" "
                           f"({total} linhas).")
                st.rerun()

        with st.expander("Importar de arquivo"):
            _importar("Arquivo de pessoas", ABA_PESSOAS, COLS_PESSOAS, "pes",
                      maiusculas=True)

    # ------------------------------------------------------------- Motivos
    with ab4:
        st.caption("Cada motivo é vinculado a um setor. O setor é preenchido "
                   "automaticamente no registro da ocorrência.")
        editado = st.data_editor(
            base_motivos(), num_rows="dynamic", use_container_width=True,
            hide_index=True,
            column_config={"SETOR": st.column_config.SelectboxColumn(
                options=SETORES)})
        if st.button("Salvar motivos na planilha", type="primary"):
            if _exigir_sheets():
                total = salvar_aba(ABA_MOTIVOS,
                                   editado.dropna(subset=["MOTIVO"]),
                                   COLS_MOTIVOS)
                st.success(f"{total} motivos salvos na aba \"{ABA_MOTIVOS}\".")
                st.rerun()


# ==========================================================================
# APLICAÇÃO
# ==========================================================================
st.set_page_config(page_title="Sistema de Ocorrências", page_icon="📦",
                   layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>
    .block-container {padding-top: 2.2rem; max-width: 1400px;}
    [data-testid="stMetricValue"] {font-size: 1.5rem;}
</style>""", unsafe_allow_html=True)


def verificar_arquivos() -> bool:
    """Só exige os arquivos do repositório quando o Sheets ainda não tem base."""
    if usando_sheets():
        return True
    faltando = [n for n, c in {"produtos.csv": ARQ_PRODUTOS,
                               "motivos.csv": ARQ_MOTIVOS,
                               "pessoas.json": ARQ_PESSOAS}.items()
                if not c.exists()]
    if not faltando:
        return True

    st.error("Arquivos de base não encontrados: " + ", ".join(faltando))
    st.markdown(
        "Envie estes arquivos para o repositório dentro da pasta **`dados/`**, "
        "ao lado do `app.py` — ou configure o Google Sheets e importe as bases "
        "pela aba Bases.")
    with st.expander("O que o app está enxergando no servidor"):
        st.write(f"Pasta do app: `{BASE_DIR}`")
        st.write("Arquivos na raiz:")
        st.code("\n".join(sorted(p.name for p in BASE_DIR.iterdir())) or "(vazio)")
        st.write("Arquivos em dados/:")
        st.code("\n".join(sorted(p.name for p in DADOS.iterdir()))
                if DADOS.exists() else "a pasta dados/ não existe")
    return False


def main():
    usuario = tela_login()
    if not usuario:
        st.stop()

    with st.sidebar:
        st.markdown("### 📦 Ocorrências")
        st.markdown(f"**{usuario['nome']}**")
        st.caption(f"{usuario.get('cargo') or usuario['perfil'].title()}")
        st.divider()
        opcoes = ["Registrar", "Consultar", "Painel"]
        if pode_editar(usuario):
            opcoes.append("Bases")
        pagina = st.radio("Navegação", opcoes, label_visibility="collapsed")
        st.divider()
        st.caption(f"Armazenamento: {nome_backend()}")
        if not usando_sheets():
            st.warning("Modo CSV local. Configure o Google Sheets nos secrets "
                       "para não perder dados no Streamlit Cloud.", icon="⚠️")
        if st.button("Atualizar bases", use_container_width=True):
            ler_aba.clear()
            limpar_cache_bases()
            st.rerun()
        if st.button("Sair", use_container_width=True):
            st.session_state.pop("usuario", None)
            st.rerun()

    if not verificar_arquivos():
        st.stop()

    {"Registrar": pagina_registrar, "Consultar": pagina_consultar,
     "Painel": pagina_painel, "Bases": pagina_bases}[pagina](usuario)


if __name__ == "__main__":
    main()
