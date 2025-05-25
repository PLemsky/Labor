# projekt_gpx_viewer/design.py
from nicegui import ui
from typing import Optional # Wird für optionale Parameter in Hilfsfunktionen benötigt

# Definiere Farbkonstanten, die im gesamten Projekt verwendet werden können
PRIMARY_COLOR_HEX = '#1B5E20'    # Dunkles Grün (aus einem früheren Beispiel von dir)
SECONDARY_COLOR_HEX = '#A5D6A7' # Helles Grün für Akzente
BACKGROUND_COLOR_HEX = '#E8F5E9'# Sehr helles Grün / fast weiß für den Body-Hintergrund
TEXT_COLOR_HEX = '#1B2E23'      # Dunkler Text

def apply_design_and_get_header():
    """
    Wendet globale Design-Anpassungen an (z.B. Body-Stil) und gibt
    eine Funktion zurück, die den Standard-Header der Anwendung rendert.
    Das Leaflet-CSS wird jetzt direkt in main.py hinzugefügt.
    """

    # Globale CSS-Stile über ui.add_head_html()
    # Hier können z.B. Schriftarten oder der Body-Hintergrund global gesetzt werden.
    ui.add_head_html(f"""
    <style>
        :root {{
            --color-primary: {PRIMARY_COLOR_HEX};
            --color-secondary: {SECONDARY_COLOR_HEX};
            --color-background: {BACKGROUND_COLOR_HEX};
            --color-text: {TEXT_COLOR_HEX};
        }}
        body {{
            background-color: var(--color-background) !important;
            color: var(--color-text) !important;
            font-family: 'Roboto', -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
        }}
        /* Beispiel für eine benutzerdefinierte Button-Klasse, falls benötigt */
        .btn-custom-primary {{
            background-color: var(--color-primary) !important;
            color: white !important;
        }}
        .btn-custom-primary:hover {{
            filter: brightness(1.15);
        }}
    </style>
    """)

    # Definiere Standardfarben für NiceGUI-Komponenten
    # Diese werden von vielen ui-Elementen als Standard verwendet, wenn keine Farbe explizit gesetzt wird.
    ui.colors(primary=PRIMARY_COLOR_HEX,
              secondary=SECONDARY_COLOR_HEX,
              accent=PRIMARY_COLOR_HEX, # Oft ist Accent = Primary eine gute Wahl
              positive='#2E7D32', # Dunkleres Grün für positive Nachrichten
              negative='#C62828', # Standard Rot für Fehler
              info='#0277BD',    # Standard Blau für Info
              warning='#FF8F00')  # Standard Orange für Warnungen

    # Funktion, die den Header rendert
    def app_header():
        with ui.header(elevated=True).style(f'background-color: {PRIMARY_COLOR_HEX};').classes('items-center justify-between text-white q-py-sm q-px-md'):
            with ui.row().classes('items-center'):
                ui.icon('route', size='lg').classes('q-mr-sm') # Angepasste Größe und Margin
                ui.label('GPX Track Manager').classes('text-h5 font-bold') # Größer und fetter
            # Hier könnten weitere Header-Elemente hinzukommen (z.B. Menü, User-Profil)
            # with ui.row():
            #     ui.button('Login', on_click=lambda: ui.notify('Login geklickt')).props('flat color=white')

    return app_header


# Optionale Hilfsfunktionen für gestylte UI-Elemente (Beispiele)
def create_primary_button(text: str, on_click=None, icon: Optional[str] = None):
    """Erstellt einen Button im primären Farbschema."""
    return ui.button(text, on_click=on_click, icon=icon).props(f'color=primary text-color=white unelevated')

def create_small_input(label: str, value: str = ''):
    """Erstellt ein kleines, dichtes Input-Feld."""
    return ui.input(label=label, value=value).props('dense outlined stack-label').classes('min-w-[150px]')