from message import MESSAGE
from textual.app import App
from textual.containers import Center, Middle
from textual.widgets import Footer, Header, Static


class DemoApp(App[None]):
    CSS_PATH = "main.tcss"

    def compose(self):
        yield Header()
        yield Middle(Center(Static(f"Minimal textual-hmr demo\n\n{MESSAGE}", id="panel")))
        yield Footer()


if __name__ == "__main__":
    DemoApp().run()
