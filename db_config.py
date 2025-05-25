from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, func, event
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.engine import Engine
from pathlib import Path
import json
from datetime import datetime
from typing import List, Optional, Tuple, Any, Dict # <--- Dict HIER HINZUGEFÜGT
import traceback

# --- Konfiguration ---
# BASE_DIR zeigt auf das Verzeichnis, in dem db_config.py liegt.
# Wenn db_config.py im Hauptprojektverzeichnis (projekt_gpx_viewer) liegt:
BASE_DIR = Path(__file__).resolve().parent
# Wenn db_config.py in einem Unterordner (z.B. 'database') liegt, dann:
# BASE_DIR = Path(__file__).resolve().parent.parent # Um zum Projektroot zu gelangen

GPX_UPLOAD_DIR = BASE_DIR / "gpx_uploads"
GPX_UPLOAD_DIR.mkdir(parents=True, exist_ok=True) # Sicherstellen, dass das Verzeichnis existiert

DATABASE_URL = f"sqlite:///{BASE_DIR / 'tracks_sqlalchemy.db'}" # Neuer DB-Name zur Unterscheidung

# --- Datenbank Setup ---
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}) # Wichtig für SQLite mit Threads/Async
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Aktiviert FOREIGN KEY Unterstützung für SQLite bei jeder Verbindung
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

# --- Datenbank Modell (TrackDB) ---
class TrackDB(Base):
    __tablename__ = "tracks"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    original_filename = Column(String, nullable=True)
    stored_filename = Column(String, nullable=False, unique=True)
    distance_km = Column(Float, nullable=True)
    upload_date = Column(DateTime, default=func.now())
    track_date = Column(DateTime, nullable=True) 
    labels = Column(Text, default="[]") 
    gpx_parsed_total_ascent = Column(Float, nullable=True) 
    gpx_parsed_total_descent = Column(Float, nullable=True)

def create_db_tables():
    Base.metadata.create_all(bind=engine)
    print("SQLAlchemy Datenbanktabellen überprüft/erstellt.")

create_db_tables()

# --- Datenbank CRUD Operationen ---

def add_track(
    db: Session,
    parsed_gpx_data: Dict[str, Any], # Hier wurde Dict verwendet
    gpx_file_content_bytes: bytes
) -> Optional[int]:
    # ... (Rest der Funktion bleibt gleich)
    original_filename = parsed_gpx_data.get("original_filename", "unknown.gpx")
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
    safe_original_filename = "".join(c if c.isalnum() or c in ('.', '_', '-') else '_' for c in original_filename)
    stored_filename = f"{timestamp}_{safe_original_filename}"
    filepath_on_server = GPX_UPLOAD_DIR / stored_filename

    try:
        with open(filepath_on_server, "wb") as f:
            f.write(gpx_file_content_bytes)

        db_track = TrackDB(
            name=parsed_gpx_data.get("track_name", "Unbenannter Track"),
            original_filename=original_filename,
            stored_filename=stored_filename, 
            distance_km=parsed_gpx_data.get("distance_km"),
            track_date=parsed_gpx_data.get("track_date"), 
            labels=json.dumps(parsed_gpx_data.get("labels_list", [])), 
            gpx_parsed_total_ascent=parsed_gpx_data.get("total_ascent"),
            gpx_parsed_total_descent=parsed_gpx_data.get("total_descent")
        )
        db.add(db_track)
        db.commit()
        db.refresh(db_track)
        print(f"Track '{db_track.name}' (ID: {db_track.id}) in DB gespeichert. Datei: {stored_filename}")
        return db_track.id
    except Exception as e:
        db.rollback()
        print(f"Fehler beim Hinzufügen des Tracks zur DB: {e}")
        traceback.print_exc() 
        if filepath_on_server.exists():
            try:
                filepath_on_server.unlink()
                print(f"Aufräumen: Datei {filepath_on_server} nach DB-Fehler gelöscht.")
            except Exception as e_file:
                print(f"Fehler beim Aufräumen der Datei {filepath_on_server}: {e_file}")
        return None

def get_track_details(db: Session, track_id: int) -> Optional[TrackDB]:
    return db.query(TrackDB).filter(TrackDB.id == track_id).first()

def get_filtered_tracks(
    db: Session,
    start_date_str: Optional[str] = None,
    end_date_str: Optional[str] = None,
    label_filter_list: Optional[List[str]] = None
) -> List[TrackDB]:
    query = db.query(TrackDB)
    try:
        if start_date_str:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            query = query.filter(TrackDB.track_date >= start_date)
        if end_date_str:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            query = query.filter(TrackDB.track_date <= end_date)
        
        if label_filter_list:
            for label in label_filter_list:
                query = query.filter(TrackDB.labels.like(f'%"{label}"%'))
        
        return query.order_by(TrackDB.track_date.desc().nullslast(), TrackDB.id.desc()).all()
    except ValueError as ve:
        print(f"Datumsformatfehler im Filter: {ve}")
        return db.query(TrackDB).order_by(TrackDB.track_date.desc().nullslast(), TrackDB.id.desc()).all()
    except Exception as e:
        print(f"Fehler beim Filtern von Tracks: {e}")
        traceback.print_exc() # Hier wird traceback verwendet
        return []

def update_track_details(db: Session, track_id: int, new_name: str, new_labels_list: List[str]) -> bool:
    track = db.query(TrackDB).filter(TrackDB.id == track_id).first()
    if track:
        track.name = new_name.strip() if new_name.strip() else "Unbenannter Track"
        track.labels = json.dumps(sorted(list(set(new_labels_list))))
        try:
            db.commit()
            print(f"Track ID {track_id} aktualisiert.")
            return True
        except Exception as e:
            db.rollback()
            print(f"Fehler beim Aktualisieren von Track ID {track_id}: {e}")
            traceback.print_exc() # Hier wird traceback verwendet
            return False
    return False

def delete_track_by_id_with_file(db: Session, track_id: int) -> Optional[str]:
    track = db.query(TrackDB).filter(TrackDB.id == track_id).first()
    if track:
        track_name_for_notification = track.name
        filepath_to_delete = GPX_UPLOAD_DIR / track.stored_filename
        try:
            db.delete(track)
            db.commit()
            print(f"Track ID {track_id} aus DB gelöscht.")
            if filepath_to_delete.exists():
                filepath_to_delete.unlink()
                print(f"Datei {filepath_to_delete} gelöscht.")
            else:
                print(f"Datei {filepath_to_delete} für Track ID {track_id} nicht gefunden.")
            return track_name_for_notification
        except Exception as e:
            db.rollback()
            print(f"Fehler beim Löschen von Track ID {track_id} (oder zugehöriger Datei): {e}")
            traceback.print_exc() # Hier wird traceback verwendet
            return None
    return None

def delete_multiple_tracks_with_files(db: Session, track_ids: List[int]) -> Tuple[int, List[str]]:
    if not track_ids:
        return 0, []
    
    tracks_to_delete = db.query(TrackDB).filter(TrackDB.id.in_(track_ids)).all()
    deleted_count = 0
    errors = []

    for track in tracks_to_delete:
        filepath_to_delete = GPX_UPLOAD_DIR / track.stored_filename
        try:
            db.delete(track)
            if filepath_to_delete.exists():
                filepath_to_delete.unlink()
            # Zähle erst nach erfolgreichem Commit, aber hier erstmal optimistisch
            # deleted_count += 1 # Besser erst nach dem Commit zählen oder im Commit-Block anpassen
        except Exception as e:
            errors.append(f"Fehler bei Track ID {track.id} ('{track.name}'): {e}")
            print(f"Fehler beim Vorbereiten des Löschens für Track ID {track.id}: {e}")
    
    try:
        db.commit()
        deleted_count = len(tracks_to_delete) # Wenn Commit erfolgreich, wurden alle markierten gelöscht
        print(f"{deleted_count} Tracks und ihre Dateien (falls vorhanden) aus DB gelöscht.")
    except Exception as e_commit:
        db.rollback()
        errors.append(f"Fehler beim finalen DB-Commit: {e_commit}")
        print(f"Fehler beim finalen DB-Commit für Massenlöschung: {e_commit}")
        traceback.print_exc() # Hier wird traceback verwendet
        return 0, errors + [f"DB-Commit fehlgeschlagen, keine Tracks gelöscht."] 

    return deleted_count, errors


def get_all_unique_labels(db: Session) -> List[str]:
    all_labels_json_strings = db.query(TrackDB.labels).distinct().all()
    unique_labels_set = set()
    for (labels_json,) in all_labels_json_strings:
        if labels_json and labels_json.strip() and labels_json != "null":
            try:
                labels_list = json.loads(labels_json)
                for label in labels_list:
                    if label and label.strip():
                        unique_labels_set.add(label.strip())
            except json.JSONDecodeError:
                print(f"Warnung: Ungültiger JSON-String für Labels in DB gefunden: {labels_json}")
    return sorted(list(unique_labels_set))

def get_gpx_filepath(db: Session, track_id: int) -> Optional[Path]:
    stored_filename = db.query(TrackDB.stored_filename).filter(TrackDB.id == track_id).scalar()
    if stored_filename:
        return GPX_UPLOAD_DIR / stored_filename
    return None