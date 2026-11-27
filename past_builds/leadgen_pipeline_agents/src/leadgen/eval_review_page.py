"""Weekly eval review page on its own: `leadgen evals review`. The dashboard shows the same view
as its Eval review tab."""

import streamlit

from leadgen.eval_review_view import render_eval_review

streamlit.set_page_config(page_title="Eval review", layout="wide")
streamlit.title("Eval review")
render_eval_review()
