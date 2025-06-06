# projekt_gpx_viewer/main.py
from nicegui import ui, app, Client
from datetime import datetime
import json
from typing import List, Dict, Any, Optional, Tuple, Set # Set hier importiert, wird aber nur intern verwendet
import asyncio
import traceback
from pathlib import Path

# Lokale Importe
import db_config
import gpx_utils
import design

# Global variables for the edit dialog (not stored in app.storage)
edit_dialog_instance: Optional[ui.dialog] = None
name_input_for_dialog: Optional[ui.input] = None
labels_input_for_dialog: Optional[ui.input] = None
current_editing_track_id: Optional[int] = None

# Lade Leaflet CSS explizit
ui.add_head_html('<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>')
styled_header = design.apply_design_and_get_header()


async def init_app_storage(client: Client):
    await client.connected()
    db = db_config.SessionLocal()
    try:
        app.storage.user.setdefault('tracks_in_table_data', []) # List[Dict]
        # all_db_labels_options wird dynamisch in update_all_db_labels_options_ui gesetzt
        app.storage.user.setdefault('selected_track_ids_list', []) # <--- Speichere IDs als LISTE
        app.storage.user.setdefault('filter_date_from_str', None) # String
        app.storage.user.setdefault('filter_date_to_str', None) # String
        app.storage.user.setdefault('filter_labels_list', []) # List[str]
        app.storage.user.setdefault('map_needs_initial_fit', True) # Boolean
    finally:
        db.close()
    
    print("DEBUG: init_app_storage complete.")
    # Initiales Laden der Tracks nach kurzer Verzögerung
    ui.timer(0.5, initial_load_and_map_setup, once=True)


async def initial_load_and_map_setup():
    print("DEBUG: initial_load_and_map_setup called.")
    map_view = app.storage.client.get('ui_map_view')
    if map_view:
        print("DEBUG: Running invalidateSize on map_view.")
        # Leaflet braucht manchmal einen Tick, nachdem es im DOM ist
        map_view.run_method('invalidateSize')
        await asyncio.sleep(0.1) 
    else:
        print("WARNING: map_view not found in client storage during initial_load_and_map_setup.")
    
    await load_tracks_from_db_and_refresh_ui(is_initial_load=True)


@ui.page('/')
async def main_page(client: Client):
    global edit_dialog_instance, name_input_for_dialog, labels_input_for_dialog

    styled_header()

    with ui.column().classes('w-full p-4 items-center gap-4'):
        with ui.row().classes('w-full max-w-6xl justify-center gap-4'):
            with ui.card().classes('w-full md:w-1/2 lg:w-1/3 shadow-lg'):
                with ui.card_section(): ui.label('GPX Hochladen').classes('text-lg font-semibold')
                ui.separator()
                with ui.card_section():
                    ui.upload(label='GPX-Datei(en) auswählen oder hierhin ziehen',
                               on_upload=handle_gpx_upload, multiple=True, auto_upload=True) \
                        .props('accept=".gpx" flat bordered').classes('w-full')
            
            with ui.card().classes('w-full md:w-1/2 lg:w-2/3 shadow-lg'):
                with ui.card_section(): ui.label('Filter').classes('text-lg font-semibold')
                ui.separator()
                with ui.card_section(), ui.column().classes('gap-2'):
                    with ui.row().classes('w-full items-center gap-2'):
                        date_from_input = ui.date(
                            value=app.storage.user.get('filter_date_from_str'),
                            on_change=lambda e: update_filter_settings('date_from', e.value)
                        ).props('label="Von Datum" dense outlined clearable').classes('flex-grow')
                        
                        date_to_input = ui.date(
                            value=app.storage.user.get('filter_date_to_str'),
                            on_change=lambda e: update_filter_settings('date_to', e.value)
                        ).props('label="Bis Datum" dense outlined clearable').classes('flex-grow')

                    # Optionen werden dynamisch in update_all_db_labels_options_ui gesetzt
                    label_select_ui = ui.select(
                        options=[], # Initial leer, wird gefüllt
                        label='Nach Label(s) filtern',
                        value=app.storage.user.get('filter_labels_list', []),
                        multiple=True, clearable=True,
                        on_change=lambda e: update_filter_settings('labels', e.value)
                    ).props('dense outlined').classes('w-full')
                    
                    ui.button('Filter zurücksetzen', icon='restart_alt',
                              on_click=lambda: reset_all_filters(date_from_input, date_to_input, label_select_ui)) \
                        .props('flat dense color=grey-7').classes('mt-2 self-start')

        with ui.splitter(value=60).classes('w-full max-w-7xl h-[calc(100vh-250px)] min-h-[400px] mt-4 shadow-md') as splitter:
            with splitter.before, ui.column().classes('w-full h-full p-0'):
                map_card = ui.card().classes('w-full h-full p-0 m-0 overflow-hidden')
                with map_card:
                    map_view_ui = ui.leaflet(center=(50.0, 10.0), zoom=5, draw_control=False) \
                                    .classes('w-full h-full')
                    # Tile Layer wird in update_map_and_related_stats gesetzt/erneuert

                    with ui.element('div').style('position: absolute; bottom: 10px; left: 10px; background-color: rgba(255,255,255,0.8); padding: 5px; border-radius: 3px; z-index: 1000; box-shadow: 0 0 5px rgba(0,0,0,0.3);'):
                        stats_total_distance_ui = ui.label("Gesamtstrecke: 0.00 km")
                        stats_total_ascent_ui = ui.label("Gesamtanstieg: 0 m")

            with splitter.after, ui.column().classes('w-full h-full'):
                with ui.card().classes('w-full h-full flex flex-col'):
                    with ui.card_section():
                        with ui.row().classes('w-full justify-between items-center'):
                            ui.label('Meine Tracks').classes('text-lg font-semibold')
                            delete_selected_button_ui = ui.button(icon='delete_sweep',
                                                                 on_click=confirm_delete_selected_tracks,
                                                                 color='negative') \
                                .props('flat dense round').tooltip('Ausgewählte Tracks löschen')
                            # Bind enabled state to the LIST of selected IDs
                            delete_selected_button_ui.bind_enabled_from(app.storage.user, 'selected_track_ids_list', backward=lambda ids_list: bool(ids_list))
                    
                    columns_def = [
                        {'name': 'id', 'label': 'ID', 'field': 'id', 'sortable': True, 'align': 'left', 'style': 'width: 5%'},
                        {'name': 'name', 'label': 'Name', 'field': 'name', 'sortable': True, 'align': 'left'},
                        {'name': 'distance', 'label': 'Distanz', 'field': 'distance_str', 'sortable': True, 'align': 'right'},
                        {'name': 'date', 'label': 'Datum', 'field': 'track_date_str', 'sortable': True, 'align': 'left'},
                        {'name': 'labels', 'label': 'Labels', 'field': 'labels_str', 'align': 'left', 'style': 'max-width: 150px; white-space: normal;'},
                        {'name': 'actions', 'label': '', 'field': 'id', 'align': 'right', 'style': 'width: 10%'}
                    ]
                    track_table_ui = ui.table(columns=columns_def,
                                           rows=app.storage.user.get('tracks_in_table_data', []),
                                           row_key='id', selection='multiple',
                                           on_select=handle_table_selection_change, # This updates selected_track_ids_list
                                           # Initial selection from storage
                                           
                                           pagination={'rowsPerPage': 100, 'sortBy': 'track_date', 'descending': True}) \
                        .classes('w-full flex-grow').props('flat dense bordered virtual-scroll')
                    
                    track_table_ui.add_slot('body-cell-actions', '''
                        <q-td :props="props" style="text-align: right; padding: 0;">
                            <q-btn flat dense round icon="edit" @click="() => $parent.$emit('editTrack', props.row)" class="q-mr-xs" />
                            <q-btn flat dense round icon="delete" @click="() => $parent.$emit('deleteTrack', props.row.id)" />
                        </q-td>''')
                    track_table_ui.on('editTrack', lambda e: open_track_edit_dialog(e.args['id']))
                    track_table_ui.on('deleteTrack', lambda e: confirm_delete_single_track(e.args))

                    ui.separator().classes('my-2')
                    elevation_chart_container_ui = ui.column().classes('w-full min-h-[150px] h-40')

        with ui.dialog().props('persistent') as local_dialog_ref, ui.card().style('min-width: 350px'):
            edit_dialog_instance = local_dialog_ref
            with ui.card_section(): ui.label('Track Bearbeiten').classes('text-h6')
            ui.separator()
            name_input_for_dialog = ui.input('Name').props('outlined dense').classes('w-full')
            labels_input_for_dialog = ui.input('Labels (Komma-getrennt)', placeholder="z.B. Alpen, Radtour") \
                .props('outlined dense').classes('w-full my-2')
            with ui.card_actions().props('align=right'):
                ui.button('Abbrechen', on_click=edit_dialog_instance.close).props('flat color=grey')
                ui.button('Speichern', on_click=save_edited_track_details).props('color=primary')

    app.storage.client['ui_map_view'] = map_view_ui
    app.storage.client['ui_track_table'] = track_table_ui
    app.storage.client['ui_stats_dist'] = stats_total_distance_ui
    app.storage.client['ui_stats_asc'] = stats_total_ascent_ui
    app.storage.client['ui_elevation_chart_container'] = elevation_chart_container_ui
    app.storage.client['ui_label_select_filter'] = label_select_ui


async def handle_gpx_upload(e: Any):
    filename = e.name
    content_bytes = e.content.read()
    
    parsed_data = gpx_utils.parse_gpx_data_from_content(filename, content_bytes)
    if not parsed_data:
        ui.notify(f"Konnte GPX-Daten aus {filename} nicht verarbeiten.", type='negative')
        return

    db = db_config.SessionLocal()
    try:
        new_track_id = db_config.add_track(
            db=db,
            parsed_gpx_data=parsed_data,
            gpx_file_content_bytes=content_bytes
        )
        if new_track_id:
            ui.notify(f"Track '{parsed_data.get('track_name', filename)}' hochgeladen.", type='positive')
            
            # <--- HIER: Neuen Track als Liste von IDs speichern --->
            app.storage.user['selected_track_ids_list'] = [new_track_id]
            
            app.storage.user['map_needs_initial_fit'] = True
            await load_tracks_from_db_and_refresh_ui() # Ruft intern update_all_db_labels_options_ui
        else:
            ui.notify("Fehler beim Speichern des Tracks.", type='negative')
    except Exception as ex_upload:
        print(f"ERROR during handle_gpx_upload: {ex_upload}")
        traceback.print_exc()
        ui.notify(f"Schwerer Fehler beim Upload: {ex_upload}", type='negative', multi_line=True)
    finally:
        db.close()


def format_track_for_display(track_db_obj: db_config.TrackDB) -> Dict[str, Any]:
    labels_list = json.loads(track_db_obj.labels) if track_db_obj.labels and track_db_obj.labels != "null" else []
    return {
        'id': track_db_obj.id,
        'name': track_db_obj.name or "Unbenannt",
        'distance_km': track_db_obj.distance_km,
        'distance_str': f"{track_db_obj.distance_km or 0:.2f} km",
        'track_date': track_db_obj.track_date,
        'track_date_str': track_db_obj.track_date.strftime('%Y-%m-%d') if track_db_obj.track_date else "N/A",
        'labels_list': labels_list,
        'labels_str': ", ".join(labels_list) if labels_list else "",
        'stored_filename': track_db_obj.stored_filename,
        'total_ascent': track_db_obj.gpx_parsed_total_ascent,
    }

async def load_tracks_from_db_and_refresh_ui(is_initial_load: bool = False):
    print(f"DEBUG: load_tracks_from_db_and_refresh_ui. Initial: {is_initial_load}")
    db = db_config.SessionLocal()
    try:
        date_from = app.storage.user.get('filter_date_from_str')
        date_to = app.storage.user.get('filter_date_to_str')
        labels = app.storage.user.get('filter_labels_list', [])
        
        tracks_from_db = db_config.get_filtered_tracks(db, date_from, date_to, labels)
        app.storage.user['tracks_in_table_data'] = [format_track_for_display(t) for t in tracks_from_db]
        
        track_table = app.storage.client.get('ui_track_table')
        if track_table:
            # 1. Aktualisiere die Zeilen der Tabelle mit den (möglicherweise gefilterten) Daten
            current_table_rows_data = app.storage.user.get('tracks_in_table_data', [])
            track_table.rows = current_table_rows_data
            
            # 2. Hole die Liste der IDs, die ausgewählt sein sollen, aus dem Storage
            ids_to_select_from_storage = app.storage.user.get('selected_track_ids_list', [])
            
            # 3. Finde die tatsächlichen Zeilen-Objekte (Dictionaries) in den
            #    aktuellen Tabellenzeilen, die diesen IDs entsprechen.
            #    Dies stellt sicher, dass nur Zeilen ausgewählt werden, die auch angezeigt werden.
            selected_row_objects_for_table = [
                row_data for row_data in current_table_rows_data 
                if row_data['id'] in ids_to_select_from_storage
            ]
            
            # 4. Setze die .selected Eigenschaft der Tabelle mit der Liste der Zeilen-Objekte
            track_table.selected = selected_row_objects_for_table
            
            # 5. (Optional, aber gute Praxis) Aktualisiere die Liste der ausgewählten IDs im Storage,
            #    damit sie konsistent mit dem ist, was tatsächlich ausgewählt und sichtbar ist.
            app.storage.user['selected_track_ids_list'] = [row['id'] for row in selected_row_objects_for_table]
            
            # 6. Fordere ein UI-Update für die Tabelle an (obwohl .selected Zuweisung dies oft impliziert)
            track_table.update()

            print(f"DEBUG: Table rows updated: {len(track_table.rows)} tracks.")
            print(f"DEBUG:   Requested selected IDs from storage: {ids_to_select_from_storage}")
            print(f"DEBUG:   Actual selected row objects set in table: {len(track_table.selected)} items.")
            print(f"DEBUG:   Updated selected_track_ids_list in storage: {app.storage.user['selected_track_ids_list']}")
        else:
            print("WARNING: ui_track_table not in client storage for update.")

        # Labels nach Laden der Tracks aktualisieren
        update_all_db_labels_options_ui(db_session=db)

        await update_map_and_related_stats(is_initial_map_fit=(is_initial_load or app.storage.user.get('map_needs_initial_fit', False)))
        if is_initial_load or app.storage.user.get('map_needs_initial_fit', False):
            app.storage.user['map_needs_initial_fit'] = False
    except Exception as e_load:
        print(f"ERROR in load_tracks_from_db_and_refresh_ui: {e_load}")
        traceback.print_exc()
        ui.notify(f"Fehler beim Laden/Aktualisieren der Tracks: {e_load}", type='negative')
    finally:
        db.close()

def update_all_db_labels_options_ui(db_session: Optional[db_config.Session] = None):
    db_to_use = db_session if db_session else db_config.SessionLocal()
    try:
        new_labels = db_config.get_all_unique_labels(db_to_use)
        # Speichere die Label-Optionen selbst NICHT im persistenten Storage,
        # da sie sich ständig ändern und frisch aus der DB geholt werden.
        # Stattdessen speichern wir sie in einem temporären Attribut oder nutzen sie direkt.
        # app.storage.user['all_db_labels_options'] = new_labels # <-- Diese Zeile entfernen/ändern

        label_select = app.storage.client.get('ui_label_select_filter')
        if label_select:
            # Hier verwenden wir die neue Liste direkt
            label_select.options = new_labels
            
            # Behalte nur gültige Labels im Filter bei
            current_filter_value = app.storage.user.get('filter_labels_list', [])
            valid_filter_value = [val for val in current_filter_value if val in new_labels]
            app.storage.user['filter_labels_list'] = valid_filter_value # Update Storage
            label_select.set_value(valid_filter_value)
            # label_select.update() # set_value sollte UI aktualisieren
        print(f"DEBUG: Updated label filter options: {new_labels}")
    finally:
        if not db_session:
            db_to_use.close()


async def update_filter_settings(filter_type: str, value: Any):
    if filter_type == 'date_from': app.storage.user['filter_date_from_str'] = value
    elif filter_type == 'date_to': app.storage.user['filter_date_to_str'] = value
    elif filter_type == 'labels': app.storage.user['filter_labels_list'] = value if isinstance(value, list) else ([value] if value is not None else [])
    
    app.storage.user['map_needs_initial_fit'] = True
    await load_tracks_from_db_and_refresh_ui()

async def reset_all_filters(date_from_ui: ui.date, date_to_ui: ui.date, label_select_ui: ui.select):
    app.storage.user['filter_date_from_str'] = None
    app.storage.user['filter_date_to_str'] = None
    app.storage.user['filter_labels_list'] = []
    date_from_ui.set_value(None)
    date_to_ui.set_value(None)
    label_select_ui.set_value([]) # Setze auch UI Wert
    
    app.storage.user['map_needs_initial_fit'] = True
    await load_tracks_from_db_and_refresh_ui()

async def handle_table_selection_change(e: Any):
    # e.selection ist eine Liste von ausgewählten Zeilen-Objekten
    selected_ids_set = {item['id'] for item in e.selection} if e.selection else set()
    
    # <--- HIER: Selektion als LISTE im Storage speichern --->
    app.storage.user['selected_track_ids_list'] = list(selected_ids_set)
    
    print(f"DEBUG handle_table_selection_change: selected_track_ids_list = {app.storage.user['selected_track_ids_list']}")
    await update_map_and_related_stats(is_initial_map_fit=False) # Bei Selektion nicht unbedingt neu fitten, außer es ist der erste Track

async def update_map_and_related_stats(is_initial_map_fit: bool = False):
    map_view = app.storage.client.get('ui_map_view')
    stats_dist = app.storage.client.get('ui_stats_dist')
    stats_asc = app.storage.client.get('ui_stats_asc')
    chart_container = app.storage.client.get('ui_elevation_chart_container')
    
    # <--- HIER: Selektion als LISTE aus Storage holen und zu Set konvertieren für Logik --->
    selected_ids_list = app.storage.user.get('selected_track_ids_list', [])
    selected_ids_set: Set[int] = set(selected_ids_list)
    
    if not map_view: print("CRITICAL: map_view not found."); return
    print(f"DEBUG update_map_and_stats: IDs: {selected_ids_list}, InitialFit: {is_initial_map_fit}")

    map_view.clear_layers()
    map_view.tile_layer(
        url_template='https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        options={
            'attribution': '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        }
    ) # Standard-Tile-Layer neu setzen!

    if chart_container: chart_container.clear()
    else: print("WARNING: chart_container not found.")

    if not selected_ids_set: # Check if set is empty
        if stats_dist: stats_dist.set_text("Gesamtstrecke: 0.00 km")
        if stats_asc: stats_asc.set_text("Gesamtanstieg: 0 m")
        return # No tracks selected -> Nothing to plot or summarize

    # Details für ausgewählte Tracks aus dem Table-Store holen (effizienter)
    tracks_in_table_data = app.storage.user.get('tracks_in_table_data', [])
    selected_track_display_data = [t for t in tracks_in_table_data if t['id'] in selected_ids_set]

    total_dist_km = 0.0
    total_asc_m = 0.0
    all_track_points_for_bounds = []
    
    db = db_config.SessionLocal() # Session für Dateipfade benötigt
    try:
        for track_data in selected_track_display_data:
            total_dist_km += track_data.get('distance_km', 0.0) or 0
            total_asc_m += track_data.get('total_ascent', 0.0) or 0

            gpx_file_path = db_config.get_gpx_filepath(db, track_data['id'])
            if gpx_file_path and gpx_file_path.exists():
                points = gpx_utils.get_points_from_gpx_file(str(gpx_file_path))
                if points:
                    map_view.generic_layer(name='polyline', args=[points, {'color': design.PRIMARY_COLOR_HEX, 'weight': 3}])
                    all_track_points_for_bounds.extend(points)
            else:
                print(f"WARNING: GPX file path not found or file does not exist for track ID {track_data['id']} (Filename: {track_data.get('stored_filename', 'N/A')}).")
    finally:
        db.close()

    if stats_dist: stats_dist.set_text(f"Gesamtstrecke: {total_dist_km:.2f} km")
    if stats_asc: stats_asc.set_text(f"Gesamtanstieg: {total_asc_m:.0f} m")

    if all_track_points_for_bounds and map_view:
        bounds = gpx_utils.get_bounds_for_points(all_track_points_for_bounds)
        if bounds:
            print(f"DEBUG: Calculated bounds: {bounds}")
            if is_initial_map_fit or len(selected_ids_set) > 0: # Fitten, wenn initial ODER wenn was ausgewählt ist
                try:
                    map_view.run_method('invalidateSize')
                    await asyncio.sleep(0.1)
                    map_view.run_method('fitBounds', [[bounds[0][0], bounds[0][1]], [bounds[1][0], bounds[1][1]]], timeout=5.0)
                    print("DEBUG: fitBounds call attempted.")
                except Exception as e_fit:
                    print(f"ERROR calling fitBounds: {e_fit}")
        else: print("DEBUG: Could not calculate bounds.")
    elif map_view: # Kein Track ausgewählt ODER keine Punkte im ausgewählten Track
        print("DEBUG: No points to fit bounds, setting default center/zoom.")
        map_view.set_center((50.0, 10.0))
        map_view.set_zoom(5)

    # Höhenprofil
    if len(selected_track_display_data) == 1 and chart_container:
        track_for_profile = selected_track_display_data[0]
        db_chart = db_config.SessionLocal()
        try:
            gpx_file_path_chart = db_config.get_gpx_filepath(db_chart, track_for_profile['id'])
            if gpx_file_path_chart and gpx_file_path_chart.exists():
                elevation_chart_data = gpx_utils.get_elevation_data_for_chart(str(gpx_file_path_chart))
                if elevation_chart_data:
                    with chart_container:
                        ui.echart({
                            "title": {"text": f"Höhenprofil: {track_for_profile.get('name', 'Unbenannt')}", "left": 'center', "textStyle": {"fontSize": 14}},
                            "grid": {"left": '60px', "right": '30px', "bottom": '50px', "top": '50px', "containLabel": False},
                            "tooltip": {"trigger": 'axis', "axisPointer": {"type": 'cross'}},
                            "xAxis": {"type": 'category', "boundaryGap": False, "data": elevation_chart_data["categories"], "name": "Distanz (km)", "nameLocation": "middle", "nameGap": 25},
                            "yAxis": {"type": 'value', "name": "Höhe (m)", "axisLabel": {"formatter": '{value} m'}},
                            "series": [{"name": "Höhe", "type": 'line', "smooth": True, "data": elevation_chart_data["series_data"],
                                        "lineStyle": {"color": design.PRIMARY_COLOR_HEX},
                                        "areaStyle": {"color": design.SECONDARY_COLOR_HEX, "opacity": 0.3}}]
                        }).classes('w-full h-full')
                else:
                    with chart_container: ui.label("Keine Höhendaten verfügbar.").classes('p-2 text-center text-grey')
            else:
                with chart_container: ui.label("GPX-Datei für Höhenprofil nicht gefunden.").classes('p-2 text-center text-grey')
        finally:
            db_chart.close()
    elif chart_container:
        chart_container.clear()


def open_track_edit_dialog(track_id: int):
    global current_editing_track_id, name_input_for_dialog, labels_input_for_dialog, edit_dialog_instance
    if not all([name_input_for_dialog, labels_input_for_dialog, edit_dialog_instance]):
        ui.notify("Edit-Dialog nicht bereit.", type='error'); return

    db = db_config.SessionLocal()
    try:
        track_to_edit = db_config.get_track_details(db, track_id)
        if track_to_edit:
            current_editing_track_id = track_id
            name_input_for_dialog.set_value(track_to_edit.name)
            labels_list = json.loads(track_to_edit.labels) if track_to_edit.labels and track_to_edit.labels != "null" else []
            labels_input_for_dialog.set_value(", ".join(labels_list))
            edit_dialog_instance.open()
        else:
            ui.notify(f"Track ID {track_id} nicht gefunden.", type='warning')
    finally:
        db.close()

async def save_edited_track_details():
    global current_editing_track_id, name_input_for_dialog, labels_input_for_dialog, edit_dialog_instance
    if current_editing_track_id is None or not all([name_input_for_dialog, labels_input_for_dialog, edit_dialog_instance]):
        return
    new_name = name_input_for_dialog.value
    labels_str = labels_input_for_dialog.value
    labels_list = [label.strip() for label in labels_str.split(',') if label.strip()]
    
    db = db_config.SessionLocal()
    try:
        success = db_config.update_track_details(db, current_editing_track_id, new_name, labels_list)
        if success:
            ui.notify(f"Track '{new_name}' aktualisiert.", type='positive')
            edit_dialog_instance.close()
            # app.storage.user['map_needs_initial_fit'] = False # Keine Änderung der Map-Ansicht nötig, nur Daten/Labels ändern sich
            await load_tracks_from_db_and_refresh_ui() # Ruft intern update_all_db_labels_options_ui
        else:
            ui.notify(f"Fehler beim Aktualisieren von Track ID {current_editing_track_id}.", type='negative')
    finally:
        db.close()
    current_editing_track_id = None


async def confirm_delete_single_track(track_id: int):
    db = db_config.SessionLocal()
    try:
        track_detail = db_config.get_track_details(db, track_id)
        if not track_detail:
            ui.notify(f"Track ID {track_id} nicht gefunden.", type='warning'); return
        with ui.dialog() as conf_dialog, ui.card():
            ui.label(f"Track '{track_detail.name}' wirklich löschen?").classes('m-4 text-lg')
            with ui.row().classes('w-full justify-end gap-2 p-2'):
                ui.button("Abbrechen", on_click=conf_dialog.close).props('flat')
                ui.button("Löschen", on_click=lambda: delete_single_track_confirmed(track_id, conf_dialog), color='negative')
        await conf_dialog
    finally:
        db.close()

async def delete_single_track_confirmed(track_id: int, dialog_ref: ui.dialog):
    dialog_ref.close()
    db = db_config.SessionLocal()
    try:
        deleted_track_name = db_config.delete_track_by_id_with_file(db, track_id)
        if deleted_track_name:
            ui.notify(f"Track '{deleted_track_name}' gelöscht.", type='positive')
            
            # <--- HIER: Ausgewählte IDs als LISTE aus Storage holen, modifizieren (als Set), als LISTE speichern --->
            current_selection_list = app.storage.user.get('selected_track_ids_list', [])
            current_selection_set = set(current_selection_list)
            current_selection_set.discard(track_id)
            app.storage.user['selected_track_ids_list'] = list(current_selection_set)
            # <--- ENDE Selektion Aktualisierung --->

            app.storage.user['map_needs_initial_fit'] = True # Map sollte neu fitten, da Track weg ist
            await load_tracks_from_db_and_refresh_ui() # Ruft intern update_all_db_labels_options_ui
        else:
            ui.notify(f"Fehler beim Löschen von Track ID {track_id}.", type='negative')
    finally:
        db.close()

async def confirm_delete_selected_tracks():
    selected_ids_list = app.storage.user.get('selected_track_ids_list', [])
    if not selected_ids_list: return
    with ui.dialog() as conf_dialog, ui.card():
        ui.label(f"{len(selected_ids_list)} ausgewählte Tracks wirklich löschen?").classes('m-4 text-lg')
        with ui.row().classes('w-full justify-end gap-2 p-2'):
            ui.button("Abbrechen", on_click=conf_dialog.close).props('flat')
            ui.button(f"{len(selected_ids_list)} Löschen", on_click=lambda: delete_multiple_tracks_confirmed(list(selected_ids_list), conf_dialog), color='negative')
    await conf_dialog

async def delete_multiple_tracks_confirmed(track_ids_to_delete: List[int], dialog_ref: ui.dialog):
    dialog_ref.close()
    if not track_ids_to_delete: return
    db = db_config.SessionLocal()
    try:
        num_deleted, errors = db_config.delete_multiple_tracks_with_files(db, track_ids_to_delete)
        if num_deleted > 0: ui.notify(f"{num_deleted} Tracks gelöscht.", type='positive')
        if errors: ui.notify(f"{len(errors)} Fehler beim Löschen.", type='warning', multi_line=True)
        if num_deleted == 0 and not errors: ui.notify("Keine Tracks gelöscht.", type='info')

        # <--- HIER: Ausgewählte IDs löschen und leere Liste im Storage speichern --->
        app.storage.user['selected_track_ids_list'] = []
        # <--- ENDE Selektion Aktualisierung --->

        app.storage.user['map_needs_initial_fit'] = True # Map sollte neu fitten
        await load_tracks_from_db_and_refresh_ui() # Ruft intern update_all_db_labels_options_ui
    finally:
        db.close()

ui.run(title="GPX Track Manager", storage_secret="DEIN_EINZIGARTIGER_SECRET_KEY_HIER", reload=False, port=8081)