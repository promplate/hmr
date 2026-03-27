from message import MESSAGE
from textual.app import App
from textual.containers import Center, Middle
from textual.widgets import Footer, Header, Static


class DemoApp(App[None]):
    CSS = """
    Screen { align: center middle; }
    #panel { width: 56; padding: 1 3; border: round $accent; background: $surface; }
    """

    def compose(self):
        yield Header()
        yield Middle(Center(Static(f"Minimal textual-hmr demo\n\n{MESSAGE}", id="panel")))
        yield Footer()


if __name__ == "__main__":
    DemoApp().run()
