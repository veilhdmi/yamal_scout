"""Streamlit chat UI for Yamal Scout. Run with: streamlit run app.py"""

import streamlit as st

from agent import chat

TOOL_LABELS = {
    "query_stats": "Querying BigQuery",
    "predict_stat": "Running the forecast model",
    "get_next_matches": "Checking upcoming fixtures",
    "get_recent_news": "Reading recent news",
    "web_search": "Searching the web",
}
EXAMPLES = [
    "¿Cuántos tiros al arco crees que haga Lamine en su próximo partido?",
    "¿Qué probabilidad hay de que marque en el próximo partido de Champions?",
    "Compara sus números de esta temporada con la 2024-25",
    "¿Qué se dice de Lamine esta semana?",
]

st.set_page_config(page_title="Yamal Scout", page_icon="⚽")
st.title("⚽ Yamal Scout")
st.caption("Match stats in BigQuery · count-model forecasts · live news · powered by Claude")

if "api_messages" not in st.session_state:
    st.session_state.api_messages = []  # full transcript sent to the API (incl. tool calls / thinking)
    st.session_state.display = []  # (role, text) pairs shown in the UI

with st.sidebar:
    st.subheader("Try asking")
    for ex in EXAMPLES:
        if st.button(ex, use_container_width=True):
            st.session_state.pending = ex
    if st.button("Clear chat", type="secondary"):
        st.session_state.api_messages, st.session_state.display = [], []
        st.rerun()

for role, text in st.session_state.display:
    st.chat_message(role).markdown(text)

prompt = st.chat_input("Ask about Lamine Yamal...") or st.session_state.pop("pending", None)
if prompt:
    st.chat_message("user").markdown(prompt)
    st.session_state.display.append(("user", prompt))
    st.session_state.api_messages.append({"role": "user", "content": prompt})

    with st.chat_message("assistant"):
        with st.status("Thinking...", expanded=False) as status:
            answer = chat(
                st.session_state.api_messages,
                on_tool=lambda name, args: status.write(f"🔧 {TOOL_LABELS.get(name, name)} `{args}`"),
            )
            status.update(label="Done", state="complete")
        st.markdown(answer)
    st.session_state.display.append(("assistant", answer))
