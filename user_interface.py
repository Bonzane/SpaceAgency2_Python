from textual.app import App
from textual.widgets import Header, Footer, Static

class MyApp(App):
    def compose(self):
        yield Header()
        yield Static("Hello, this is a TUI app!", id="main")
        yield Footer()

MyApp().run()
