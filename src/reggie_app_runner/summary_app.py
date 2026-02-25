import argparse
import json
from pathlib import Path

from dash import Dash, Input, Output, dcc, html
from waitress import serve


"""Dash summary app that renders protected and public URLs for app-runner."""


def main() -> int:
    parser = argparse.ArgumentParser(prog="reggie-app-runner-summary")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--state-file", type=str, required=True)
    parser.add_argument("--base-path", type=str, default="/")
    args = parser.parse_args()

    state_file = Path(args.state_file)
    base_path = _normalize_base_path(args.base_path)
    app = _build_app(state_file=state_file, base_path=base_path)
    serve(app.server, host="0.0.0.0", port=args.port)
    return 0


def _build_app(state_file: Path, base_path: str) -> Dash:
    app = Dash(
        __name__,
        requests_pathname_prefix=base_path,
    )

    app.layout = html.Div(
        [
            dcc.Interval(id="refresh-interval", interval=5000, n_intervals=0),
            html.Div(id="summary-content"),
        ],
        style=_PAGE_STYLE,
    )
    app.index_string = _INDEX_TEMPLATE

    @app.callback(Output("summary-content", "children"), Input("refresh-interval", "n_intervals"))
    def _render(_n_intervals: int):
        payload = _read_state(state_file)
        protected_rows = payload.get("protected_urls", [])
        public_rows = payload.get("public_urls", [])

        card_items = [
            _metric_card("Protected URLs", str(len(protected_rows))),
            _metric_card("Public URLs", str(len(public_rows))),
            _metric_card("Last Refresh", payload.get("updated_at", "Unknown")),
        ]

        return html.Div(
            [
                html.Div(
                    [
                        html.H1("Reggie App Runner", style=_TITLE_STYLE),
                        html.P(
                            "Routes, local endpoints, and public tunnel URLs.",
                            style=_SUBTITLE_STYLE,
                        ),
                    ]
                ),
                html.Div(card_items, style=_METRIC_GRID_STYLE),
                _section("Protected URLs", protected_rows, "No protected routes are active."),
                _section("Public URLs", public_rows, "No public tunnels are active."),
            ]
        )

    return app


def _section(title: str, rows: list[dict[str, str]], empty_message: str):
    return html.Div(
        [
            html.H2(title, style=_SECTION_TITLE_STYLE),
            _urls_table(rows, empty_message),
        ],
        style=_SECTION_STYLE,
    )


def _urls_table(rows: list[dict[str, str]], empty_message: str):
    if not rows:
        return html.Div(empty_message, style=_EMPTY_STATE_STYLE)
    header = html.Tr(
        [html.Th("Name"), html.Th("URL"), html.Th("Route")],
        style=_TABLE_HEADER_ROW_STYLE,
    )
    body_rows = []
    for row in rows:
        url_value = row.get("url", "")
        body_rows.append(
            html.Tr(
                [
                    html.Td(row.get("name", ""), style=_CELL_STYLE),
                    html.Td(_link(url_value), style=_CELL_STYLE),
                    html.Td(row.get("route", ""), style=_CELL_STYLE),
                ],
                style=_TABLE_ROW_STYLE,
            )
        )
    return html.Table(
        [html.Thead(header), html.Tbody(body_rows)],
        style=_TABLE_STYLE,
    )


def _link(url: str):
    if not url:
        return html.Span("-", style={"color": "#6b7280"})
    return html.A(url, href=url, target="_blank", rel="noopener noreferrer", style=_LINK_STYLE)


def _metric_card(label: str, value: str):
    return html.Div(
        [
            html.Div(label, style=_CARD_LABEL_STYLE),
            html.Div(value, style=_CARD_VALUE_STYLE),
        ],
        style=_CARD_STYLE,
    )


def _read_state(state_file: Path) -> dict:
    if not state_file.is_file():
        return {"protected_urls": [], "public_urls": [], "updated_at": "Not available"}
    try:
        payload = json.loads(state_file.read_text())
        if "updated_at" not in payload:
            payload["updated_at"] = "Not available"
        return payload
    except Exception:
        return {"protected_urls": [], "public_urls": [], "updated_at": "Invalid state"}


def _normalize_base_path(path: str) -> str:
    path = path.strip() or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    if not path.endswith("/"):
        path = f"{path}/"
    return path


_PAGE_STYLE = {
    "backgroundColor": "#0b1220",
    "color": "#e5e7eb",
    "fontFamily": "Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
    "minHeight": "100vh",
    "padding": "24px",
}
_TITLE_STYLE = {
    "fontSize": "32px",
    "fontWeight": "700",
    "margin": "0 0 8px 0",
}
_SUBTITLE_STYLE = {
    "fontSize": "15px",
    "color": "#9ca3af",
    "margin": "0 0 20px 0",
}
_METRIC_GRID_STYLE = {
    "display": "grid",
    "gridTemplateColumns": "repeat(auto-fit, minmax(200px, 1fr))",
    "gap": "12px",
    "marginBottom": "20px",
}
_CARD_STYLE = {
    "backgroundColor": "#111827",
    "border": "1px solid #1f2937",
    "borderRadius": "10px",
    "padding": "14px",
}
_CARD_LABEL_STYLE = {
    "fontSize": "12px",
    "textTransform": "uppercase",
    "letterSpacing": "0.06em",
    "color": "#9ca3af",
}
_CARD_VALUE_STYLE = {
    "fontSize": "22px",
    "fontWeight": "700",
    "marginTop": "6px",
}
_SECTION_STYLE = {
    "backgroundColor": "#111827",
    "border": "1px solid #1f2937",
    "borderRadius": "10px",
    "padding": "16px",
    "marginBottom": "16px",
}
_SECTION_TITLE_STYLE = {
    "fontSize": "20px",
    "fontWeight": "600",
    "margin": "0 0 12px 0",
}
_EMPTY_STATE_STYLE = {
    "color": "#9ca3af",
    "fontStyle": "italic",
    "padding": "8px 2px",
}
_TABLE_STYLE = {
    "width": "100%",
    "borderCollapse": "collapse",
    "backgroundColor": "#0f172a",
    "borderRadius": "8px",
    "overflow": "hidden",
}
_TABLE_HEADER_ROW_STYLE = {
    "textAlign": "left",
    "borderBottom": "1px solid #1f2937",
}
_TABLE_ROW_STYLE = {
    "borderBottom": "1px solid #1f2937",
}
_CELL_STYLE = {
    "padding": "10px 8px",
    "fontSize": "14px",
}
_LINK_STYLE = {
    "color": "#60a5fa",
    "textDecoration": "none",
    "fontWeight": "500",
}

_INDEX_TEMPLATE = """<!DOCTYPE html>
<html>
  <head>
    {%metas%}
    <title>{%title%}</title>
    {%favicon%}
    {%css%}
    <style>
      html, body {
        margin: 0;
        padding: 0;
        background: #0b1220;
      }
    </style>
  </head>
  <body>
    {%app_entry%}
    <footer>
      {%config%}
      {%scripts%}
      {%renderer%}
    </footer>
  </body>
</html>"""


if __name__ == "__main__":
    raise SystemExit(main())
