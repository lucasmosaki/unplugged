import time
import readchar
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

def clear():
    console.clear()

def typewriter(text, delay=0.05):
    for char in text:
        console.print(char, end='', style="bold cyan")
        time.sleep(delay)
    console.print() 

def bar(value, maximum=30, color='red'):
    return f"[{color}]" + "█" * value + "[/]" + "░" * (maximum - value)

def show_scenario(s, artist, round_no, total):
    body =(
        f"[bold]{artist['name']}[/bold] ({artist['genre']})\n\n"
        f"{s['connection']}\n\n"
        f"[italic]{s['headline']}[/italic]\n\n"
        f"Seriousness  {bar(s['seriousness'], color='red')}\n"
        f"Evidence     {bar(s['evidence'], color='cyan')}\n"
        f"Artist profits from your streams: {s['profit']}"
    )
    console.print(Panel(body, title=f"Round {round_no}/{total}"))

def choice():
    console.print("[bold]Keep Listening, Pause, Drop[/bold]")
    options = {"k": "keep", "p": "pause", "d": "drop"}
    while True:
        key = readchar.readkey().lower
        if key in options:
            return options[key]