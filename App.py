"""Sistema de Ocorrências - aplicação principal."""
import streamlit as st

import auth
import storage
from paginas import admin_bases, consultar, dashboard, registrar

st.set_page_config(
    page_title="Sistema de Ocorrências",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 2.2rem; max-width: 1400px;}
      [data-testid="stMetricValue"] {font-size: 1.5rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


def main():
    usuario = auth.login()
    if not usuario:
        st.stop()

    with st.sidebar:
        st.markdown("### 📦 Ocorrências")
        st.markdown(f"**{usuario['nome']}**")
        st.caption(f"Perfil: {usuario['perfil']}")
        st.divider()

        opcoes = ["Registrar", "Consultar", "Painel"]
        if auth.eh_admin(usuario):
            opcoes.append("Bases")
        pagina = st.radio("Navegação", opcoes, label_visibility="collapsed")

        st.divider()
        st.caption(f"Armazenamento: {storage.nome_backend()}")
        if not storage.usando_sheets():
            st.warning(
                "Modo CSV local. Configure o Google Sheets nos secrets "
                "para não perder dados no Streamlit Cloud.",
                icon="⚠️",
            )
        if st.button("Sair", use_container_width=True):
            auth.logout()

    if pagina == "Registrar":
        registrar.render(usuario)
    elif pagina == "Consultar":
        consultar.render(usuario)
    elif pagina == "Painel":
        dashboard.render(usuario)
    else:
        admin_bases.render(usuario)


if __name__ == "__main__":
    main()
