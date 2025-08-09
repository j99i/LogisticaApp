import sys
import subprocess
import os

# --- INICIO: INSTALACIÓN AUTOMÁTICA DE DEPENDENCIAS ---
def install_dependencies():
    """
    Instala las dependencias necesarias para la aplicación.
    Esto es un workaround para entornos que no permiten requirements.txt.
    """
    print("Verificando e instalando dependencias...")
    packages = [
        "Flask",
        "Flask-SQLAlchemy",
        "Flask-Login",
        "Flask-Session",
        "redis",
        "pandas",
        "msal",
        "requests",
        "numpy",
        "gunicorn",
        "python-dotenv",
        "openpyxl" # Necesario para que pandas lea archivos .xlsx
    ]
    for package in packages:
        try:
            print(f"Instalando {package}...")
            # Se usa sys.executable para asegurar que se use el pip del entorno correcto
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        except subprocess.CalledProcessError:
            print(f"ADVERTENCIA: No se pudo instalar {package}. Puede que ya esté instalado o haya un problema de red.")
        except Exception as e:
            print(f"Error inesperado al instalar {package}: {e}")
    print("✅ Verificación de dependencias completada.")

# Ejecutar la instalación de dependencias
install_dependencies()
# --- FIN: INSTALACIÓN AUTOMÁTICA DE DEPENDENCIAS ---


import json
import uuid
import traceback
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, jsonify, request, abort, send_file, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from flask_session import Session
import pandas as pd
from datetime import datetime, timedelta
import io
import msal
import requests
import base64
import numpy as np
import redis

# --- Modificación para Disco Persistente de Render ---
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

# --- 1. CONFIGURACIÓN ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', os.urandom(24))
app.config['SUPER_USER_EMAIL'] = os.getenv('SUPER_USER_EMAIL', 'j.ortega@minmerglobal.com')

# --- CONFIGURACIÓN DE FLASK-SESSION CON REDIS ---
redis_url = os.getenv('REDIS_URL')
if redis_url:
    print("Configurando sesión con Redis...")
    app.config['SESSION_TYPE'] = 'redis'
    app.config['SESSION_PERMANENT'] = False
    app.config['SESSION_USE_SIGNER'] = True
    app.config['SESSION_REDIS'] = redis.from_url(redis_url)
else:
    print("ADVERTENCIA: REDIS_URL no encontrada. Usando sesión de 'filesystem'.")
    app.config['SESSION_TYPE'] = 'filesystem'
    app.config['SESSION_FILE_DIR'] = os.path.join(DATA_DIR, '.flask_session')
    os.makedirs(app.config['SESSION_FILE_DIR'], exist_ok=True)

Session(app)

# --- CONFIGURACIÓN DE SHAREPOINT Y MSAL ---
CLIENT_ID = os.getenv("CLIENT_ID", "de80bcd3-0096-4eb9-bee8-b1ef0350481f")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TENANT_ID = os.getenv("TENANT_ID", "0e29816e-116e-4ab2-bf42-d2b815e86284")

if not CLIENT_SECRET:
    print("ERROR CRÍTICO: La variable de entorno CLIENT_SECRET no está configurada.")

SCOPES = ["User.Read", "Sites.ReadWrite.All"]
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
REDIRECT_PATH = "/get_token"

# --- CONFIGURACIÓN DE LA BASE DE DATOS ---
DB_PATH = os.path.join(DATA_DIR, "seguimiento_v2.db")
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- CONFIGURACIÓN DE FLASK-LOGIN ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- LÓGICA PARA MANEJAR PORTALES ---
PORTALES_FILE_PATH = os.path.join(DATA_DIR, 'portales.json')

def sanitize_and_get_ids(data):
    changes_made = False
    for cliente_data in data:
        if 'cliente' in cliente_data and isinstance(cliente_data, dict):
            cliente_data['nombre'] = cliente_data.pop('cliente')
            changes_made = True
        if 'id' not in cliente_data:
            cliente_data['id'] = str(uuid.uuid4())
            changes_made = True
        for portal in cliente_data.get('portales', []):
            if 'id' not in portal:
                portal['id'] = str(uuid.uuid4())
                changes_made = True
    return changes_made

def load_portales_data():
    try:
        with open(PORTALES_FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if sanitize_and_get_ids(data):
            save_portales_data(data)
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_portales_data(data):
    with open(PORTALES_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# --- 2. MODELOS DE BASE DE DATOS ---
user_permissions = db.Table('user_permissions',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('permission_id', db.Integer, db.ForeignKey('permission.id'), primary_key=True)
)
user_channels = db.Table('user_channels',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('channel_id', db.Integer, db.ForeignKey('channel.id'), primary_key=True)
)
class Permission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200))
class Channel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    nombre = db.Column(db.String(100), nullable=True)
    rol = db.Column(db.String(50), nullable=False, default='normal')
    permissions = db.relationship('Permission', secondary=user_permissions, lazy='subquery',
                                    backref=db.backref('users', lazy=True))
    allowed_channels = db.relationship('Channel', secondary=user_channels, lazy='subquery',
                                       backref=db.backref('users', lazy=True))
    def has_permission(self, perm_name):
        if self.rol == 'super': return True
        return any(p.name == perm_name for p in self.permissions)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

class Bloque(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    creado_en = db.Column(db.DateTime, default=datetime.utcnow)
    seguimientos = db.relationship('Seguimiento', backref='bloque', lazy='dynamic')

class Seguimiento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    identificador_unico = db.Column(db.String(100), unique=True, nullable=False)
    cliente = db.Column(db.String(150))
    canal = db.Column(db.String(100))
    so = db.Column(db.String(100))
    factura = db.Column(db.String(100))
    fecha_entrega = db.Column(db.String(50))
    horario = db.Column(db.String(50))
    localidad_destino = db.Column(db.String(200))
    no_botellas = db.Column(db.Integer)
    no_cajas = db.Column(db.Integer)
    subtotal = db.Column(db.Float)
    estado = db.Column(db.String(100), nullable=False, default='Pendiente')
    notas = db.Column(db.Text)
    archivada = db.Column(db.Boolean, default=False)
    bloque_id = db.Column(db.Integer, db.ForeignKey('bloque.id'))
    tareas = db.relationship('Tarea', backref='seguimiento', lazy=True, cascade="all, delete-orphan",
                             primaryjoin="Seguimiento.identificador_unico==Tarea.seguimiento_id")

class Tarea(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descripcion = db.Column(db.String(200), nullable=False)
    completado = db.Column(db.Boolean, default=False)
    seguimiento_id = db.Column(db.String(100), db.ForeignKey('seguimiento.identificador_unico'), nullable=False)

class HistorialOrden(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    orden_compra = db.Column(db.String(100), index=True)
    cliente = db.Column(db.String(150))
    canal = db.Column(db.String(100), nullable=True)
    so = db.Column(db.String(100), nullable=True)
    factura = db.Column(db.String(100), nullable=True)
    fecha_entrega = db.Column(db.String(50))
    horario = db.Column(db.String(50), nullable=True)
    estado_final = db.Column(db.String(100))
    fecha_archivado = db.Column(db.DateTime, default=datetime.utcnow)
    localidad_destino = db.Column(db.String(200), nullable=True)
    no_botellas = db.Column(db.Integer, nullable=True)
    no_cajas = db.Column(db.Integer, nullable=True)
    subtotal = db.Column(db.Float, nullable=True)
    notas = db.Column(db.Text, nullable=True)
    def to_dict(self, for_excel=False):
        data = {
            'id': self.id,
            'Orden de compra': self.orden_compra, 'Cliente': self.cliente, 'Canal': self.canal, 'SO': self.so,
            'Factura': self.factura, 'Fecha Entrega': self.fecha_entrega, 'Horario': self.horario,
            'Estado Final': self.estado_final, 'Fecha Archivado': self.fecha_archivado.strftime('%Y-%m-%d %H:%M'),
            'Localidad Destino': self.localidad_destino, 'No. Botellas': self.no_botellas,
            'No. Cajas': self.no_cajas, 'Subtotal': self.subtotal, 'Notas': self.notas
        }
        if not for_excel:
            return {'id': self.id, 'Orden de compra': self.orden_compra, 'Cliente': self.cliente, 'Fecha Entrega': self.fecha_entrega, 'Estado Final': self.estado_final}
        return data

# --- LÓGICA DE SINCRONIZACIÓN Y DATOS ---
def obtener_datos_sharepoint_con_auth():
    print("ADVERTENCIA: Usando datos de ejemplo. Implementar la lógica real de SharePoint.")
    try:
        df = pd.read_excel('General.xlsx', sheet_name='General')
        return df
    except FileNotFoundError:
        print("Archivo 'General.xlsx' no encontrado. Devolviendo DataFrame vacío.")
        return pd.DataFrame()

def sincronizar_con_sharepoint():
    df_excel = obtener_datos_sharepoint_con_auth()
    if df_excel.empty:
        print("No se encontraron datos en SharePoint o el archivo está vacío.")
        return {"success": False, "message": "No se encontraron datos."}

    df_excel.columns = df_excel.columns.str.strip().str.lower().str.replace(' ', '_').str.replace('.', '', regex=False)
    
    required_cols = {
        'orden_compra': None, 'so': None, 'cliente': None, 'canal': None, 
        'fecha_entrega': 'Por Asignar', 'estatus': '', 'factura': None, 
        'horario': None, 'localidad_destino': None, 'no_botellas': 0, 
        'no_cajas': 0, 'subtotal': 0.0
    }
    for col, default in required_cols.items():
        if col not in df_excel.columns:
            df_excel[col] = default

    df_excel['canal'] = df_excel['canal'].str.strip().str.title()
    df_excel['fecha_entrega'] = pd.to_datetime(df_excel['fecha_entrega'], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%d').fillna('Por Asignar')
    
    df_excel['orden_compra'] = df_excel['orden_compra'].astype(str).str.strip()
    df_excel['so'] = df_excel['so'].astype(str).str.strip()
    
    df_excel['identificador_unico'] = np.where(
        df_excel['orden_compra'].isna() | (df_excel['orden_compra'] == '') | (df_excel['orden_compra'] == 'nan'),
        df_excel['so'],
        df_excel['orden_compra']
    )

    df_excel_activos = df_excel[
        (df_excel['identificador_unico'].notna()) &
        (df_excel['identificador_unico'] != '') &
        (df_excel['identificador_unico'] != 'nan') &
        (df_excel['estatus'].fillna('').str.strip() == '')
    ].copy()

    with app.app_context():
        all_unique_channels = sorted(df_excel['canal'].dropna().unique().tolist())
        existing_channels = {c.name for c in Channel.query.all()}
        for channel_name in all_unique_channels:
            if channel_name not in existing_channels:
                db.session.add(Channel(name=channel_name))
        
        ordenes_archivadas = {h.orden_compra for h in HistorialOrden.query.all()}
        
        for _, row in df_excel_activos.iterrows():
            identificador = row.get('identificador_unico')
            if not identificador or identificador in ordenes_archivadas:
                continue

            seguimiento = Seguimiento.query.filter_by(identificador_unico=identificador).first()
            
            datos_orden = {
                'cliente': row.get('cliente'), 'canal': row.get('canal'), 'so': row.get('so'),
                'factura': row.get('factura'), 'fecha_entrega': row.get('fecha_entrega'),
                'horario': row.get('horario'), 'localidad_destino': row.get('localidad_destino'),
                'no_botellas': pd.to_numeric(row.get('no_botellas'), errors='coerce'),
                'no_cajas': pd.to_numeric(row.get('no_cajas'), errors='coerce'),
                'subtotal': pd.to_numeric(row.get('subtotal'), errors='coerce')
            }

            if seguimiento:
                for key, value in datos_orden.items():
                    setattr(seguimiento, key, value)
            else:
                nuevo_seguimiento = Seguimiento(identificador_unico=identificador, **datos_orden)
                db.session.add(nuevo_seguimiento)
        
        db.session.commit()
    return {"success": True, "message": "Sincronización completada."}


# --- RUTAS DE LA API ---
@app.route('/api/logistica/sincronizar', methods=['POST'])
@login_required
def api_sincronizar():
    try:
        resultado = sincronizar_con_sharepoint()
        return jsonify(resultado)
    except Exception as e:
        print(f"ERROR CRÍTICO al sincronizar con SharePoint: {e}", flush=True)
        traceback.print_exc()
        return jsonify({"error": "No se pudo sincronizar con SharePoint."}), 500

@app.route('/api/logistica/datos')
@login_required
def get_logistica_data():
    try:
        query = Seguimiento.query.options(db.joinedload(Seguimiento.tareas))

        if current_user.rol != 'super':
            user_channels_list = [c.name for c in current_user.allowed_channels]
            query = query.filter(Seguimiento.canal.in_(user_channels_list))

        seguimientos = query.all()

        data = []
        for s in seguimientos:
            data.append({
                "Orden de compra": s.identificador_unico, "Cliente": s.cliente, "Canal": s.canal,
                "SO": s.so, "Factura": s.factura, "Fecha de entrega": s.fecha_entrega, "Horario": s.horario,
                "Localidad destino": s.localidad_destino, "No. Botellas": s.no_botellas,
                "No. Cajas": s.no_cajas, "Subtotal": s.subtotal, "Estado": s.estado,
                "Notas": s.notas, "bloque_id": s.bloque_id,
                "Tareas": [{"id": t.id, "descripcion": t.descripcion, "completado": t.completado} for t in s.tareas]
            })
        
        all_channels = [c.name for c in Channel.query.order_by(Channel.name).all()]

        return jsonify({"data": data, "channels": all_channels})
    except Exception as e:
        print(f"ERROR al obtener datos locales: {e}", flush=True)
        traceback.print_exc()
        return jsonify({"error": "No se pudieron obtener los datos de la base de datos local."}), 500

# --- RUTAS DE AUTENTICACIÓN Y SESIÓN ---
@app.route('/')
@login_required
def index():
    return render_template('index.html')

def _build_msal_app(cache=None):
    return msal.ConfidentialClientApplication(CLIENT_ID, authority=AUTHORITY, client_credential=CLIENT_SECRET, token_cache=cache)

def _build_auth_code_flow(scopes=None):
    return _build_msal_app().initiate_auth_code_flow(scopes or SCOPES, redirect_uri=url_for("get_token", _external=True))

@app.route('/login')
def login():
    session["flow"] = _build_auth_code_flow()
    return redirect(session["flow"]["auth_uri"])

@app.route(REDIRECT_PATH)
def get_token():
    try:
        cache = msal.SerializableTokenCache()
        if session.get("token_cache"):
            cache.deserialize(session["token_cache"])
        
        result = _build_msal_app(cache=cache).acquire_token_by_auth_code_flow(session.get("flow", {}), request.args)
        
        if "error" in result:
            return f"Error de login: {result.get('error_description')}", 400
        
        claims = result.get("id_token_claims")
        email, nombre = claims.get("preferred_username"), claims.get("name")
        
        user = User.query.filter_by(email=email).first()
        if not user:
            user_rol = 'super' if email.lower() == app.config['SUPER_USER_EMAIL'].lower() else 'normal'
            user = User(email=email, nombre=nombre, rol=user_rol)
            db.session.add(user)
            db.session.commit()
        
        login_user(user)
        session["token_cache"] = cache.serialize()
    except ValueError as e:
        print(f"Error en get_token: {e}")
        pass
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    logout_user()
    session.clear()
    logout_uri = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/logout?post_logout_redirect_uri={url_for('index', _external=True)}"
    return redirect(logout_uri)

@app.route('/api/me')
@login_required
def me():
    permissions = [p.name for p in (Permission.query.all() if current_user.rol == 'super' else current_user.permissions)]
    return jsonify({
        "email": current_user.email, "nombre": current_user.nombre, "rol": current_user.rol,
        "permissions": permissions, "can_manage_portals": current_user.has_permission('manage_portals')
    })


# --- RUTAS DE PORTALES ---
@app.route('/monitoreo-portales')
@login_required
def monitoreo_portales():
    return render_template('monitoreo_portales.html')

@app.route('/api/portales', methods=['GET'])
@login_required
def get_portales():
    return jsonify(load_portales_data())

# --- Rutas de Administración de Usuarios ---
@app.route('/admin/users')
@login_required
def admin_users_page():
    if not current_user.rol == 'super': abort(403)
    return render_template('admin_users.html')

@app.route('/api/users')
@login_required
def get_users():
    if not current_user.rol == 'super': abort(403)
    users = User.query.filter(User.rol != 'super').all()
    all_permissions = Permission.query.all()
    all_channels = Channel.query.order_by(Channel.name).all()
    users_data = [{"id": u.id, "nombre": u.nombre, "email": u.email, "permissions": [p.name for p in u.permissions], "allowed_channels": [c.name for c in u.allowed_channels]} for u in users]
    permissions_data = [{"name": p.name, "description": p.description} for p in all_permissions]
    return jsonify({"users": users_data, "all_permissions": permissions_data, "all_channels": [c.name for c in all_channels]})

@app.route('/api/users/<int:user_id>/permissions', methods=['POST'])
@login_required
def update_user_permissions(user_id):
    if not current_user.rol == 'super': abort(403)
    user = User.query.get_or_404(user_id)
    if user.rol == 'super': return jsonify({"success": False, "error": "No se pueden modificar los permisos del superadministrador."}), 400
    user.permissions = db.session.query(Permission).filter(Permission.name.in_(request.json.get('permissions', []))).all()
    db.session.commit()
    return jsonify({"success": True, "message": f"Permisos de {user.nombre} actualizados."})

@app.route('/api/users/<int:user_id>/channels', methods=['POST'])
@login_required
def update_user_channels(user_id):
    if not current_user.rol == 'super': abort(403)
    user = User.query.get_or_404(user_id)
    if user.rol == 'super': abort(400, "No se pueden modificar los canales del superadministrador.")
    channel_names = request.json.get('channels', [])
    user.allowed_channels = db.session.query(Channel).filter(Channel.name.in_(channel_names)).all()
    db.session.commit()
    return jsonify({"success": True, "message": f"Canales de {user.nombre} actualizados."})


# --- INICIO: Funciones de inicialización ---
def initialize_database_and_data():
    with app.app_context():
        if not os.path.exists(DB_PATH):
            print("Primera ejecución: Creando base de datos y tablas...")
            db.create_all()
            
            permissions = [
                {'name': 'update_status', 'description': 'Puede cambiar el estado de las órdenes'},
                {'name': 'edit_notes', 'description': 'Puede editar y limpiar las notas'},
                {'name': 'archive_orders', 'description': 'Puede archivar y restaurar órdenes'},
                {'name': 'group_orders', 'description': 'Puede agrupar órdenes en bloques'},
                {'name': 'manage_portals', 'description': 'Puede gestionar portales de clientes'},
                {'name': 'manage_users', 'description': 'Puede gestionar usuarios y permisos'}
            ]
            for perm_data in permissions:
                if not Permission.query.filter_by(name=perm_data['name']).first():
                    db.session.add(Permission(**perm_data))
            db.session.commit()
            print("✅ Base de datos y permisos inicializados.")
        else:
            print("La base de datos ya existe.")

        print("Iniciando sincronización de datos al arrancar la aplicación...")
        try:
            sincronizar_con_sharepoint()
        except Exception as e:
            print(f"❌ Error durante la sincronización inicial: {e}")
            traceback.print_exc()

# Llamar a la inicialización antes de arrancar la app
initialize_database_and_data()

# --- INICIO DE LA APLICACIÓN ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)

# La siguiente línea es para que Gunicorn la use en producción
server = app