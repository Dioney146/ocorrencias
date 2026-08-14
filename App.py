"""
Sistema de Ocorrências - arquivo único.

Todas as bases vivem na planilha do Google Sheets, em abas próprias:
    Diario     -> notas fiscais faturadas (fonte do preenchimento automático)
    Produtos   -> cadastro de produtos (embalagem, qt/cx, custo)
    Pessoas    -> conferentes, separadores e entregadores
    Motivos    -> motivos e seus setores
    ocorrencias-> registro gerado pelo sistema

Fluxo do registro: digita a NF -> o app busca a nota no Diario -> escolhe o
produto da nota -> informa entregador, motivo e quantidade -> finaliza.
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
    for caminho in (DADOS / nome, BASE_DIR / nome):
        if caminho.exists():
            return caminho
    return DADOS / nome


ARQ_PRODUTOS = _localizar("produtos.csv")
ARQ_MOTIVOS = _localizar("motivos.csv")
ARQ_PESSOAS = _localizar("pessoas.json")
ARQ_HISTORICO = _localizar("historico_2026.csv")
ARQ_OCORRENCIAS = DADOS / "ocorrencias.csv"  # só no modo sem Google Sheets

# --------------------------------------------------------------- abas
ABA_OCORRENCIAS = "ocorrencias"
ABA_DIARIO = "Diario"
ABA_PRODUTOS = "Produtos"
ABA_PESSOAS = "Pessoas"
ABA_MOTIVOS = "Motivos"

# Nomes alternativos aceitos na leitura (a busca ignora maiúsculas/minúsculas)
ALTERNATIVAS = {
    ABA_DIARIO: ["Diario", "Diário", "base diario"],
    ABA_PRODUTOS: ["Produtos", "base produtos"],
    ABA_PESSOAS: ["Pessoas", "base pessoas"],
    ABA_MOTIVOS: ["Motivos", "base motivos"],
}

# --------------------------------------------- colunas esperadas no Diario
# (chave interna -> possíveis nomes na planilha, em maiúsculas)
CAMPOS_DIARIO = {
    "nota_fiscal": ["NOTA FISCAL", "NOTAFISCAL", "NF"],
    "pedido": ["PEDIDO"],
    "carregamento": ["CARREGAMENTO"],
    "codigo_cliente": ["CODIGO CLIENTE", "COD CLIENTE"],
    "cliente": ["NOME CLIENTE", "CLIENTE"],
    "posicao": ["POSICAO", "POSIÇÃO"],
    "data_faturamento": ["DATA"],
    "data_entrega": ["DTENTREGA", "DT ENTREGA", "DATA ENTREGA"],
    "vendedor": ["VENDEDOR"],
    "supervisor": ["SUPERVISOR"],
    "codigo": ["CODIGO PRODUTO", "CODPROD", "CODIGO"],
    "produto": ["PRODUTO", "DESCRICAO"],
    "qt_nota": ["QT", "QTD", "QUANTIDADE"],
    "peso": ["PESO"],
    "cidade": ["CIDADE"],
    "valor_total": ["VALOR TOTAL", "VALORTOTAL"],
    "praca": ["PRACA", "PRAÇA"],
    "plano_pagamento": ["PLANOPAG", "PLANO PAGAMENTO"],
    "subcategoria": ["NOME_SUBCATEGORIA", "SUBCATEGORIA"],
}

COLS_PRODUTOS = ["CODPROD", "DESCRICAO", "EMBALAGEMMASTER", "QTUNITCX", "CUSTO"]
COLS_PESSOAS = ["CONFERENTE", "SEPARADOR", "ENTREGADOR"]
COLS_MOTIVOS = ["MOTIVO", "SETOR"]

TIPOS_PESSOA = {"conferentes": "CONFERENTE", "separadores": "SEPARADOR",
                "entregadores": "ENTREGADOR"}

MESES_PT = {1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN",
            7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ"}

# ------------------------------------------ colunas do registro gerado
COLUNAS = [
    "id", "criado_em", "mes", "data",
    "nota_fiscal", "pedido", "carregamento", "codigo_cliente", "cliente",
    "cidade", "praca", "vendedor", "supervisor",
    "data_faturamento", "data_entrega",
    "codigo", "produto", "embalagem", "qt_nota", "peso", "valor_total",
    "quantidade", "valor_unitario", "valor_financeiro",
    "motivo", "setor", "entregador", "placa", "separador", "conferente",
    "erro_separador", "bipado",
    "responsavel_registro", "responsavel_analise", "obs",
]

ROTULOS = {
    "id": "ID", "criado_em": "Criado em", "mes": "Mês/Ano", "data": "Data",
    "nota_fiscal": "Nota Fiscal", "pedido": "Pedido",
    "carregamento": "Carregamento", "codigo_cliente": "Cód. Cliente",
    "cliente": "Cliente", "cidade": "Cidade", "praca": "Praça",
    "vendedor": "Vendedor", "supervisor": "Supervisor",
    "data_faturamento": "Faturamento", "data_entrega": "Entrega",
    "codigo": "Código", "produto": "Produto", "embalagem": "Embalagem",
    "qt_nota": "Qtd na NF", "peso": "Peso", "valor_total": "Valor da NF",
    "quantidade": "Qtd Ocorrência", "valor_unitario": "Valor Un.",
    "valor_financeiro": "Valor Financeiro", "motivo": "Motivo",
    "setor": "Setor", "entregador": "Entregador", "placa": "Placa",
    "separador": "Separador", "conferente": "Conferente",
    "erro_separador": "Erro do Separador", "bipado": "Bipado",
    "responsavel_registro": "Responsável pelo Registro",
    "responsavel_analise": "Responsável / Análise", "obs": "OBS",
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
                    "login": user, "nome": d.get("nome", user.upper()),
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
    return None


def pode_editar(u: dict) -> bool:
    return u.get("perfil") in ("analista", "admin")


def eh_admin(u: dict) -> bool:
    return u.get("perfil") == "admin"


# ==========================================================================
# GOOGLE SHEETS
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
    """Busca a aba ignorando maiúsculas/minúsculas e nomes alternativos."""
    planilha = _planilha()
    existentes = {ws.title.strip().lower(): ws for ws in planilha.worksheets()}
    for candidata in ALTERNATIVAS.get(nome, [nome]):
        ws = existentes.get(candidata.strip().lower())
        if ws is not None:
            return ws
    if not criar:
        return None
    ws = planilha.add_worksheet(nome, rows=1000, cols=max(5, len(colunas or [])))
    if colunas:
        ws.update(values=[colunas], range_name="A1")
    return ws


def _letra(n: int) -> str:
    """1 -> A, 27 -> AA."""
    letra = ""
    while n > 0:
        n, resto = divmod(n - 1, 26)
        letra = chr(65 + resto) + letra
    return letra


def _tabela(valores: list) -> pd.DataFrame:
    """Monta DataFrame tolerando colunas em branco, repetidas e linhas curtas."""
    if len(valores) < 2:
        return pd.DataFrame()
    cabecalho = list(valores[0])
    largura = max(len(l) for l in valores)
    cabecalho += [""] * (largura - len(cabecalho))

    nomes, vistos = [], {}
    for i, c in enumerate(cabecalho):
        nome = str(c).strip() or f"_vazia_{i}"
        vistos[nome] = vistos.get(nome, 0) + 1
        nomes.append(nome if vistos[nome] == 1 else f"{nome}_{vistos[nome]}")

    linhas = [list(l) + [""] * (largura - len(l)) for l in valores[1:]]
    return pd.DataFrame(linhas, columns=nomes)


@st.cache_data(ttl=1800, show_spinner=False)
def ler_aba(nome: str) -> pd.DataFrame:
    if not usando_sheets():
        return pd.DataFrame()
    ws = _aba(nome)
    return _tabela(ws.get_all_values()) if ws is not None else pd.DataFrame()


def salvar_aba(nome: str, df: pd.DataFrame, colunas: list) -> int:
    df = df[colunas].fillna("").astype(str)
    linhas = [colunas] + df.values.tolist()

    ws = _aba(nome, criar=True, colunas=colunas)
    ws.clear()
    ws.resize(rows=max(len(linhas) + 50, 100), cols=len(colunas))

    LOTE = 5000
    barra = st.progress(0.0, text="Enviando para a planilha...")
    for inicio in range(0, len(linhas), LOTE):
        ws.update(values=linhas[inicio:inicio + LOTE],
                  range_name=f"A{inicio + 1}")
        barra.progress(min(1.0, (inicio + LOTE) / len(linhas)))
    barra.empty()
    ler_aba.clear()
    limpar_cache_bases()
    return len(df)


# ==========================================================================
# ABA DIARIO — busca por nota fiscal
# ==========================================================================
@st.cache_data(ttl=900, show_spinner=False)
def _diario_estrutura():
    """Cabeçalho do Diario + posição da coluna de nota fiscal."""
    if not usando_sheets():
        return None
    ws = _aba(ABA_DIARIO)
    if ws is None:
        return None
    cabecalho = [str(c).strip() for c in ws.row_values(1)]
    maiusculas = [c.upper() for c in cabecalho]

    posicao = {}
    for chave, apelidos in CAMPOS_DIARIO.items():
        for apelido in apelidos:
            if apelido in maiusculas:
                posicao[chave] = maiusculas.index(apelido)
                break
    return {"cabecalho": cabecalho, "posicao": posicao,
            "col_nf": posicao.get("nota_fiscal")}


@st.cache_data(ttl=900, show_spinner=False)
def _diario_coluna_nf() -> list:
    """Só a coluna de nota fiscal — leve mesmo com dezenas de milhares
    de linhas. É o índice usado para localizar a nota."""
    estrutura = _diario_estrutura()
    if not estrutura or estrutura["col_nf"] is None:
        return []
    ws = _aba(ABA_DIARIO)
    return [str(v).strip() for v in ws.col_values(estrutura["col_nf"] + 1)]


def buscar_nota(nota_fiscal: str) -> pd.DataFrame:
    """Devolve todos os itens da nota fiscal, lidos da aba Diario."""
    nf = str(nota_fiscal).strip()
    if not nf or not usando_sheets():
        return pd.DataFrame()

    estrutura = _diario_estrutura()
    if not estrutura or estrutura["col_nf"] is None:
        return pd.DataFrame()

    coluna = _diario_coluna_nf()
    linhas = [i + 1 for i, v in enumerate(coluna)
              if v == nf or (v.replace(".0", "") == nf)]
    if not linhas:
        return pd.DataFrame()

    # Busca apenas o intervalo onde a nota aparece, não a aba inteira.
    ws = _aba(ABA_DIARIO)
    largura = _letra(len(estrutura["cabecalho"]))
    faixa = ws.get(f"A{min(linhas)}:{largura}{max(linhas)}")
    df = _tabela([estrutura["cabecalho"]] + list(faixa))
    if df.empty:
        return df

    renomear = {}
    for chave, indice in estrutura["posicao"].items():
        if indice < len(df.columns):
            renomear[df.columns[indice]] = chave
    df = df.rename(columns=renomear)

    df = df[df["nota_fiscal"].astype(str).str.strip().str.replace(".0", "",
                                                                 regex=False) == nf]
    for c in ("qt_nota", "peso", "valor_total"):
        if c in df.columns:
            df[c] = pd.to_numeric(
                df[c].astype(str).str.replace(".", "", regex=False)
                     .str.replace(",", ".", regex=False), errors="coerce")
    return df.reset_index(drop=True)


# ==========================================================================
# DEMAIS BASES
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

    df.columns = [str(c).strip().upper() for c in df.columns]
    for alias in ("CUSTOREAL", "CUSTOULTENT"):
        if "CUSTO" not in df.columns and alias in df.columns:
            df["CUSTO"] = df[alias]
    for c in COLS_PRODUTOS:
        if c not in df.columns:
            df[c] = ""
    df = df[COLS_PRODUTOS].copy()
    df["CODPROD"] = df["CODPROD"].astype(str).str.strip().str.replace(
        ".0", "", regex=False)
    df["DESCRICAO"] = df["DESCRICAO"].fillna("").astype(str).str.strip()
    for c in ("QTUNITCX", "CUSTO"):
        df[c] = pd.to_numeric(
            df[c].astype(str).str.replace(",", ".", regex=False),
            errors="coerce")
    df["_origem"] = origem
    return df


@st.cache_data(ttl=1800, show_spinner=False)
def base_motivos() -> pd.DataFrame:
    df = ler_aba(ABA_MOTIVOS)
    if df.empty and ARQ_MOTIVOS.exists():
        df = pd.read_csv(ARQ_MOTIVOS)
    if df.empty:
        return pd.DataFrame(columns=COLS_MOTIVOS)
    df.columns = [str(c).strip().upper() for c in df.columns]
    df = df.reindex(columns=COLS_MOTIVOS).fillna("")
    return df[df["MOTIVO"].astype(str).str.strip() != ""]


@st.cache_data(ttl=1800, show_spinner=False)
def base_pessoas() -> dict:
    """Aceita a aba em dois formatos: uma coluna por tipo (CONFERENTE,
    SEPARADOR, ENTREGADOR) ou duas colunas (TIPO, NOME)."""
    df = ler_aba(ABA_PESSOAS)
    if not df.empty:
        colunas = {str(c).strip().upper(): c for c in df.columns}

        if {"TIPO", "NOME"} <= set(colunas):
            tipos = df[colunas["TIPO"]].astype(str).str.strip().str.upper()
            return {chave: sorted({str(n).strip().upper()
                                   for n in df.loc[tipos == tipo, colunas["NOME"]]
                                   if str(n).strip()})
                    for chave, tipo in TIPOS_PESSOA.items()}

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


@st.cache_data(show_spinner=False)
def base_historico() -> pd.DataFrame:
    if not ARQ_HISTORICO.exists():
        return pd.DataFrame()
    df = pd.read_csv(ARQ_HISTORICO, dtype=str)
    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    for c in ("quantidade", "custo_un"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.rename(columns={"custo_un": "valor_unitario"})
    df["valor_financeiro"] = df["quantidade"] * df["valor_unitario"]
    mot = base_motivos()
    df["setor"] = df["motivo"].map(dict(zip(mot["MOTIVO"], mot["SETOR"]))).fillna("")
    return df


def limpar_cache_bases():
    for f in (base_produtos, base_motivos, base_pessoas, base_historico,
              _diario_estrutura, _diario_coluna_nf):
        f.clear()


def buscar_produto_cadastro(codigo: str) -> dict:
    cod = str(codigo).strip().replace(".0", "")
    if not cod:
        return {}
    df = base_produtos()
    achado = df[df["CODPROD"] == cod]
    if achado.empty:
        return {}
    l = achado.iloc[0]
    return {"embalagem": str(l.get("EMBALAGEMMASTER", "") or ""),
            "qt_unit_cx": float(l["QTUNITCX"]) if pd.notna(l["QTUNITCX"]) else 1.0,
            "custo": float(l["CUSTO"]) if pd.notna(l["CUSTO"]) else None}


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
    planilha = _planilha()
    existentes = {ws.title.strip().lower(): ws for ws in planilha.worksheets()}
    ws = existentes.get(nome.strip().lower())
    if ws is None:
        ws = planilha.add_worksheet(nome, rows=2000, cols=len(COLUNAS))
        ws.update(values=[COLUNAS], range_name="A1")
    elif not ws.row_values(1):
        ws.update(values=[COLUNAS], range_name="A1")
    return ws


@st.cache_data(ttl=60, show_spinner=False)
def carregar_ocorrencias() -> pd.DataFrame:
    if usando_sheets():
        df = _tabela(_aba_ocorrencias().get_all_values())
    elif ARQ_OCORRENCIAS.exists():
        df = pd.read_csv(ARQ_OCORRENCIAS, dtype=str)
    else:
        df = pd.DataFrame(columns=COLUNAS)

    for c in COLUNAS:
        if c not in df.columns:
            df[c] = ""
    df = df[COLUNAS]
    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    for c in ("quantidade", "qt_nota", "peso", "valor_total",
              "valor_unitario", "valor_financeiro"):
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
def _cb_buscar_nota():
    nf = st.session_state.get("nf", "").strip()
    st.session_state["nota_itens"] = buscar_nota(nf) if nf else pd.DataFrame()


def pagina_registrar(usuario: dict):
    st.subheader("Registrar ocorrência")

    if not usando_sheets():
        st.error("O Google Sheets não está configurado, então o app não "
                 "consegue ler a aba Diario.")
        st.info("Abra **Diagnóstico** na barra lateral para ver exatamente o "
                "que está faltando no secrets.")
        return

    pes = base_pessoas()
    lista_motivos = sorted(base_motivos()["MOTIVO"].tolist())

    # ------------------------------------------------------- 1. nota fiscal
    c1, c2 = st.columns([1, 3])
    c1.text_input("Nota Fiscal", key="nf", on_change=_cb_buscar_nota,
                  placeholder="Digite e pressione Enter")
    itens = st.session_state.get("nota_itens", pd.DataFrame())

    if st.session_state.get("nf") and itens.empty:
        c2.warning("Nota não encontrada na aba Diario. Confira o número ou "
                   "atualize o Diario.")
        return
    if itens.empty:
        st.info("Digite o número da nota fiscal para carregar os dados.")
        return

    cab = itens.iloc[0]

    def v(campo):
        return str(cab.get(campo, "") or "")

    c2.success(f"**{v('cliente')}** · Pedido {v('pedido')} · "
               f"Carregamento {v('carregamento')} · {len(itens)} itens")

    with st.container(border=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(f"**Pedido**  \n{v('pedido')}")
        c2.markdown(f"**Carregamento**  \n{v('carregamento')}")
        c3.markdown(f"**Faturamento**  \n{v('data_faturamento')}")
        c4.markdown(f"**Entrega**  \n{v('data_entrega')}")
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(f"**Cliente**  \n{v('cliente')}")
        c2.markdown(f"**Cidade / Praça**  \n{v('cidade')} · {v('praca')}")
        c3.markdown(f"**Vendedor**  \n{v('vendedor')}")
        c4.markdown(f"**Supervisor**  \n{v('supervisor')}")

    # ----------------------------------------------------- 2. produto da NF
    st.markdown("##### Produto da nota")
    opcoes = list(itens.index)
    escolhido = st.selectbox(
        "Item", opcoes,
        format_func=lambda i: f"{itens.at[i, 'codigo']} · "
                              f"{str(itens.at[i, 'produto'])[:55]} · "
                              f"qt {itens.at[i, 'qt_nota']}")
    item = itens.loc[escolhido]

    cadastro = buscar_produto_cadastro(item.get("codigo", ""))
    qt_nota = float(item.get("qt_nota") or 0)
    valor_total = float(item.get("valor_total") or 0)
    peso = float(item.get("peso") or 0)
    unitario_nota = (valor_total / qt_nota) if qt_nota else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Qtd na nota", f"{qt_nota:,.0f}".replace(",", "."))
    c2.metric("Peso", f"{peso:,.3f}".replace(",", "X").replace(".", ",")
              .replace("X", "."))
    c3.metric("Valor do item", f"R$ {valor_total:,.2f}")
    c4.metric("Valor unitário", f"R$ {unitario_nota:,.2f}")

    # --------------------------------------------------- 3. dados da ocorrência
    st.divider()
    st.markdown("##### Dados da ocorrência")

    c1, c2, c3 = st.columns(3)
    quantidade = c1.number_input("Quantidade da ocorrência", min_value=0.0,
                                 step=1.0, value=1.0)
    opcoes_emb = EMBALAGENS_LIVRES.copy()
    emb_cad = cadastro.get("embalagem", "")
    if emb_cad and emb_cad not in opcoes_emb:
        opcoes_emb.insert(0, emb_cad)
    embalagem = c2.selectbox("Embalagem", opcoes_emb)
    unitario = c3.number_input(
        "Valor unitário (R$)", min_value=0.0, step=0.01, format="%.4f",
        value=float(unitario_nota or cadastro.get("custo") or 0.0))

    valor_financeiro = quantidade * unitario

    c1, c2, c3 = st.columns([2, 1, 1])
    motivo = c1.selectbox("Motivo", lista_motivos)
    setor = setor_do_motivo(motivo)
    c2.text_input("Setor", value=setor, disabled=True)
    c3.metric("Valor financeiro", f"R$ {valor_financeiro:,.2f}")

    c1, c2 = st.columns([2, 1])
    entregador = c1.selectbox("Entregador", [""] + list(pes["entregadores"]))
    placa = c2.text_input("Placa (opcional)").upper()

    c1, c2, c3 = st.columns(3)
    separador = c1.selectbox("Separador", [""] + list(pes["separadores"]))
    conferente = c2.selectbox("Conferente", [""] + list(pes["conferentes"]))
    responsavel_analise = c3.text_input("Responsável pela análise")

    c1, c2, c3 = st.columns(3)
    erro_separador = c1.radio("Erro do separador?", ["", "SIM", "NÃO"],
                              horizontal=True)
    bipado = c2.radio("Bipado?", ["", "SIM", "NÃO"], horizontal=True)
    data_oc = c3.date_input("Data da ocorrência", value=date.today())

    obs = st.text_area("Observações", height=80)
    st.caption(f"Registrado por **{usuario['nome']}** · "
               f"Mês/Ano: **{mes_ano(data_oc)}**")

    # ------------------------------------------------------------ 4. gravar
    if st.button("Finalizar registro", type="primary", use_container_width=True):
        if quantidade <= 0:
            st.error("Informe a quantidade da ocorrência.")
            return
        if not entregador:
            st.error("Selecione o entregador.")
            return

        novo_id = gravar_ocorrencia({
            "mes": mes_ano(data_oc), "data": data_oc.strftime("%Y-%m-%d"),
            "nota_fiscal": st.session_state["nf"].strip(),
            "pedido": v("pedido"), "carregamento": v("carregamento"),
            "codigo_cliente": v("codigo_cliente"), "cliente": v("cliente"),
            "cidade": v("cidade"), "praca": v("praca"),
            "vendedor": v("vendedor"), "supervisor": v("supervisor"),
            "data_faturamento": v("data_faturamento"),
            "data_entrega": v("data_entrega"),
            "codigo": str(item.get("codigo", "")),
            "produto": str(item.get("produto", "")).upper(),
            "embalagem": embalagem, "qt_nota": qt_nota, "peso": peso,
            "valor_total": round(valor_total, 2),
            "quantidade": quantidade,
            "valor_unitario": round(unitario, 4),
            "valor_financeiro": round(valor_financeiro, 2),
            "motivo": motivo, "setor": setor,
            "entregador": entregador, "placa": placa,
            "separador": separador, "conferente": conferente,
            "erro_separador": erro_separador, "bipado": bipado,
            "responsavel_registro": usuario["nome"],
            "responsavel_analise": responsavel_analise.upper(),
            "obs": obs.strip(),
        })
        st.success(f"Ocorrência {novo_id} registrada com sucesso.")
        for k in ("nf", "nota_itens"):
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
               .reset_index().to_excel(w, sheet_name="Resumo", index=False))
    return saida.getvalue()


# ==========================================================================
# PÁGINA: CONSULTAR
# ==========================================================================
def pagina_consultar(usuario: dict):
    st.subheader("Consultar ocorrências")
    incluir = st.toggle("Incluir histórico importado", value=True)
    df = dados_completos(incluir)
    if df.empty:
        st.info("Nenhuma ocorrência registrada ainda.")
        return

    filtrado = aplicar_filtros(df, "consulta")
    c1, c2, c3 = st.columns(3)
    c1.metric("Ocorrências", len(filtrado))
    c2.metric("Valor financeiro", f"R$ {filtrado['valor_financeiro'].sum():,.2f}")
    c3.metric("Qtd. de itens", f"{filtrado['quantidade'].sum():,.0f}")

    visiveis = ["id", "data", "nota_fiscal", "pedido", "cliente", "codigo",
                "produto", "quantidade", "motivo", "setor", "entregador",
                "separador", "conferente", "vendedor", "supervisor",
                "responsavel_registro", "valor_financeiro", "obs"]
    st.dataframe(
        filtrado[visiveis].rename(columns=ROTULOS),
        use_container_width=True, hide_index=True,
        column_config={
            "Data": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Valor Financeiro": st.column_config.NumberColumn(format="R$ %.2f"),
        })

    c1, c2 = st.columns(2)
    c1.download_button("Baixar planilha (.xlsx)", para_excel(filtrado),
                       file_name=f"ocorrencias_{date.today():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet", use_container_width=True)
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
            color=alt.Color("qtd:Q", scale=alt.Scale(scheme="blues"),
                            legend=None),
        ).properties(height=max(180, 28 * len(dados))), use_container_width=True)


def pagina_painel(usuario: dict):
    st.subheader("Painel de acompanhamento")
    incluir = st.toggle("Incluir histórico importado", value=True,
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
            x=alt.X("data:T", title=None), y=alt.Y("qtd:Q", title="Ocorrências"),
            tooltip=["data:T", "qtd:Q"]).properties(height=220),
        use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### Por motivo")
        _grafico_barra(df, "motivo", "Motivo")
        st.markdown("##### Por separador")
        _grafico_barra(df, "separador", "Separador")
        st.markdown("##### Por cliente")
        _grafico_barra(df, "cliente", "Cliente")
    with c2:
        st.markdown("##### Por setor")
        _grafico_barra(df, "setor", "Setor", top=8)
        st.markdown("##### Por entregador")
        _grafico_barra(df, "entregador", "Entregador")
        st.markdown("##### Por supervisor")
        _grafico_barra(df, "supervisor", "Supervisor")

    st.markdown("##### Por conferente")
    _grafico_barra(df, "conferente", "Conferente")
    st.markdown("##### Top 10 produtos com ocorrência")
    _grafico_barra(df, "produto", "Produto")

    with st.expander("Ver tabela mensal"):
        st.dataframe(
            df.groupby([df["data"].dt.to_period("M").astype(str), "setor"])
              .size().unstack(fill_value=0), use_container_width=True)


# ==========================================================================
# PÁGINA: BASES
# ==========================================================================
def _ler_upload(arq, maiusculas=False) -> pd.DataFrame:
    df = (pd.read_csv(arq, dtype=str) if arq.name.lower().endswith(".csv")
          else pd.read_excel(arq, dtype=str))
    df.columns = [str(c).strip().upper() if maiusculas
                  else str(c).strip().lower().replace(" ", "_")
                  for c in df.columns]
    return df.fillna("")


def _exigir_sheets() -> bool:
    if usando_sheets():
        return True
    st.error("O Google Sheets não está configurado. Sem ele a importação não "
             "é permanente.")
    return False


def pagina_bases(usuario: dict):
    st.subheader("Bases do sistema")
    if usando_sheets():
        st.info("As bases são lidas das abas da planilha. O que você importar "
                "aqui é gravado na planilha e permanece após reinícios do app.")
    else:
        st.warning("Google Sheets não configurado.", icon="⚠️")

    ab1, ab2, ab3, ab4 = st.tabs(["Diario", "Produtos", "Pessoas", "Motivos"])

    # -------------------------------------------------------------- Diario
    with ab1:
        st.markdown("A aba **Diario** é a fonte do preenchimento automático: "
                    "nota fiscal, pedido, carregamento, cliente, vendedor, "
                    "supervisor, cidade, produtos, quantidades e valores.")
        estrutura = _diario_estrutura() if usando_sheets() else None
        if not estrutura:
            st.error("Aba Diario não encontrada na planilha.")
        else:
            encontrados = list(estrutura["posicao"])
            faltando = [c for c in CAMPOS_DIARIO if c not in encontrados]
            c1, c2 = st.columns(2)
            c1.metric("Colunas reconhecidas", len(encontrados))
            c2.metric("Linhas no Diario", f"{max(len(_diario_coluna_nf()) - 1, 0):,}"
                      .replace(",", "."))
            if faltando:
                st.warning("Colunas não localizadas (ficam em branco no "
                           "registro): " + ", ".join(faltando))
            st.caption("Cabeçalho lido: " + ", ".join(estrutura["cabecalho"]))
        st.caption("O Diario é atualizado direto na planilha, pelo export do "
                   "ERP. O app lê sempre a versão mais recente — use "
                   "\"Atualizar bases\" na barra lateral se acabou de colar "
                   "dados novos.")

    # ------------------------------------------------------------ Produtos
    with ab2:
        prod = base_produtos()
        c1, c2 = st.columns(2)
        c1.metric("Produtos na base", f"{len(prod):,}".replace(",", "."))
        origem = prod["_origem"].iat[0] if len(prod) else "-"
        c2.metric("Origem", f"aba {ABA_PRODUTOS}" if origem == "planilha"
                  else "arquivo do repositório")
        busca = st.text_input("Buscar por código ou descrição")
        if busca:
            m = (prod["CODPROD"].str.contains(busca, case=False, na=False)
                 | prod["DESCRICAO"].str.contains(busca, case=False, na=False))
            st.dataframe(prod[m].head(50).drop(columns=["_origem"]),
                         use_container_width=True, hide_index=True)

        st.divider()
        st.caption("Importar substitui a aba Produtos inteira. Colunas: "
                   "CODPROD, DESCRICAO, EMBALAGEMMASTER, QTUNITCX, "
                   "CUSTO (ou CUSTOREAL).")
        arq = st.file_uploader("Arquivo de produtos (.csv ou .xlsx)",
                               type=["csv", "xlsx"], key="up_prod")
        if arq:
            novo = _ler_upload(arq, maiusculas=True)
            for alias in ("CUSTOREAL", "CUSTOULTENT"):
                if "CUSTO" not in novo.columns and alias in novo.columns:
                    novo["CUSTO"] = novo[alias]
            faltando = [c for c in COLS_PRODUTOS if c not in novo.columns]
            if faltando:
                st.error(f"Colunas faltando: {', '.join(faltando)}")
            else:
                novo = novo[COLS_PRODUTOS].drop_duplicates("CODPROD",
                                                           keep="last")
                st.success(f"{len(novo):,} produtos lidos.".replace(",", "."))
                st.dataframe(novo.head(8), use_container_width=True,
                             hide_index=True)
                if st.button("Importar para a planilha", type="primary"):
                    if _exigir_sheets():
                        total = salvar_aba(ABA_PRODUTOS, novo, COLS_PRODUTOS)
                        st.success(f"{total:,} produtos salvos na aba "
                                   f"{ABA_PRODUTOS}.".replace(",", "."))
                        st.rerun()

    # ------------------------------------------------------------- Pessoas
    with ab3:
        pes = base_pessoas()
        st.caption(f"Lendo a aba \"{ABA_PESSOAS}\". Aceita uma coluna por tipo "
                   "(CONFERENTE, SEPARADOR, ENTREGADOR) ou duas colunas "
                   "(TIPO, NOME).")
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
                salvar_aba(ABA_PESSOAS, tabela, COLS_PESSOAS)
                st.success(f"Listas salvas na aba \"{ABA_PESSOAS}\".")
                st.rerun()

    # ------------------------------------------------------------- Motivos
    with ab4:
        st.caption("Cada motivo é vinculado a um setor, preenchido "
                   "automaticamente no registro.")
        editado = st.data_editor(
            base_motivos(), num_rows="dynamic", use_container_width=True,
            hide_index=True,
            column_config={"SETOR": st.column_config.SelectboxColumn(
                options=SETORES)})
        if st.button("Salvar motivos na planilha", type="primary"):
            if _exigir_sheets():
                total = salvar_aba(ABA_MOTIVOS,
                                   editado[editado["MOTIVO"].astype(str).str.strip()
                                           != ""], COLS_MOTIVOS)
                st.success(f"{total} motivos salvos na aba \"{ABA_MOTIVOS}\".")
                st.rerun()


# ==========================================================================
# APLICAÇÃO
# ==========================================================================
st.set_page_config(page_title="Sistema de Ocorrências", page_icon="📦",
                   layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>
    .block-container {padding-top: 2.2rem; max-width: 1400px;}
    [data-testid="stMetricValue"] {font-size: 1.4rem;}
</style>""", unsafe_allow_html=True)


def diagnostico():
    """Mostra o que o app está enxergando no secrets, sem revelar valores."""
    st.markdown("##### Diagnóstico da configuração")

    try:
        chaves = list(st.secrets.keys())
    except Exception as e:
        st.error(f"Não foi possível ler o secrets: {e}")
        return

    st.write("Blocos encontrados no secrets:")
    st.code("\n".join(chaves) if chaves else "(nenhum)")

    for bloco in ("planilha", "gcp_service_account"):
        if bloco in chaves:
            st.success(f"Bloco [{bloco}] encontrado.")
        else:
            st.error(f"Bloco [{bloco}] NÃO encontrado.")

    if "planilha" in chaves:
        id_planilha = str(st.secrets["planilha"].get("id", ""))
        if not id_planilha or "COLE" in id_planilha.upper():
            st.error("O campo id da planilha ainda está com o texto de exemplo.")
        else:
            st.write(f"ID da planilha: `{id_planilha[:6]}...{id_planilha[-4:]}` "
                     f"({len(id_planilha)} caracteres)")

    if "gcp_service_account" in chaves:
        conta = dict(st.secrets["gcp_service_account"])
        obrigatorios = ["type", "project_id", "private_key", "client_email",
                        "token_uri"]
        faltando = [c for c in obrigatorios if not str(conta.get(c, "")).strip()]
        if faltando:
            st.error("Campos faltando: " + ", ".join(faltando))

        email = str(conta.get("client_email", ""))
        if email:
            st.write(f"Conta de serviço: `{email}`")
            st.caption("Esta conta precisa estar como **Editor** na planilha.")

        chave = str(conta.get("private_key", ""))
        if "COPIE" in chave.upper():
            st.error("A private_key ainda está com o texto de exemplo.")
        elif "BEGIN PRIVATE KEY" not in chave:
            st.error("A private_key não parece válida (falta o cabeçalho "
                     "BEGIN PRIVATE KEY).")
        elif "\n" not in chave:
            st.error("A private_key está sem quebras de linha. Ela precisa "
                     "conter os \\n do arquivo JSON.")
        else:
            st.success("Formato da private_key parece correto.")

    st.divider()
    if st.button("Testar conexão com a planilha", type="primary"):
        if not usando_sheets():
            st.error("O app não reconhece os dois blocos. Corrija os itens "
                     "acima antes de testar.")
            return
        try:
            planilha = _planilha()
            abas = [ws.title for ws in planilha.worksheets()]
            st.success(f"Conectado à planilha **{planilha.title}**.")
            st.write("Abas encontradas:")
            st.code("\n".join(abas))
            esperadas = {"Diario": ABA_DIARIO, "Produtos": ABA_PRODUTOS,
                         "Pessoas": ABA_PESSOAS, "Motivos": ABA_MOTIVOS}
            minusculas = {a.strip().lower() for a in abas}
            for rotulo, nome in esperadas.items():
                achou = any(alt.strip().lower() in minusculas
                            for alt in ALTERNATIVAS.get(nome, [nome]))
                (st.success if achou else st.warning)(
                    f"Aba {rotulo}: {'encontrada' if achou else 'não encontrada'}")
        except Exception as e:
            st.error(f"Falhou ao conectar: {e}")
            st.caption("Erro de permissão costuma significar que a planilha "
                       "não foi compartilhada com a conta de serviço. "
                       "Erro de chave inválida costuma ser a private_key "
                       "colada errado.")


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
            opcoes += ["Bases", "Diagnóstico"]
        pagina = st.radio("Navegação", opcoes, label_visibility="collapsed")
        st.divider()
        st.caption(f"Armazenamento: {nome_backend()}")
        if not usando_sheets():
            st.warning("Google Sheets não configurado.", icon="⚠️")
        if st.button("Atualizar bases", use_container_width=True):
            ler_aba.clear()
            limpar_cache_bases()
            st.rerun()
        if st.button("Sair", use_container_width=True):
            st.session_state.pop("usuario", None)
            st.rerun()

    if pagina == "Diagnóstico":
        diagnostico()
    else:
        {"Registrar": pagina_registrar, "Consultar": pagina_consultar,
         "Painel": pagina_painel, "Bases": pagina_bases}[pagina](usuario)


if __name__ == "__main__":
    main()
