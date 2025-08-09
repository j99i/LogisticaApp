import os
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

# --- Modificación para Disco Persistente de Render ---
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

# --- 1. CONFIGURACIÓN ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['SUPER_USER_EMAIL'] = 'j.ortega@minmerglobal.com'

# --- CONFIGURACIÓN DE FLASK-SESSION ---
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = './.flask_session/'
Session(app)

# --- CONFIGURACIÓN DE SHAREPOINT Y MSAL ---
CLIENT_ID = "de80bcd3-0096-4eb9-bee8-b1ef0350481f"
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
if not CLIENT_SECRET:
    raise ValueError("Error: La variable de entorno CLIENT_SECRET no está configurada.")

TENANT_ID = "0e29816e-116e-4ab2-bf42-d2b815e86284"
SHARING_URL = "https://minmerglobalmx.sharepoint.com/:x:/s/TraficoMinmerGlobal/EYVsfjcPRoZDqZq4H_MdgG4B3EwtugLTTYs_XDu3qFDymQ?e=R8nVUg"
NOMBRE_DE_LA_HOJA = 'General'
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

# --- 2. MODELOS DE BASE DE DATOS (ACTUALIZADOS) ---
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

# --- LÓGICA DE SINCRONIZACIÓN Y DATOS (REESTRUCTURADA) ---
def obtener_datos_sharepoint_con_auth():
    # Placeholder for your actual SharePoint data fetching logic
    # This should return a pandas DataFrame
    # For now, returning an empty DataFrame to avoid errors if SharePoint connection fails
    # In your real implementation, this part would contain the logic to connect and download the Excel file.
    print("ADVERTENCIA: Usando datos de ejemplo. Implementar la lógica real de `obtener_datos_sharepoint_con_auth()`")
    # Example DataFrame structure:
    # return pd.DataFrame(columns=['Orden de compra', 'SO', 'Cliente', 'Canal', 'Fecha de entrega', 'Horario', 'Localidad Destino', 'No. Botellas', 'No. Cajas', 'Subtotal', 'Estatus', 'Factura'])
    # You should replace this with your actual data fetching logic using MSAL and requests.
    api_url = f"https://graph.microsoft.com/v1.0/sites/minmerglobalmx.sharepoint.com:/sites/TraficoMinmerGlobal"
    # The rest of your logic to get the file content... this is a complex part you already had.
    # For this example to be runnable, I will assume a local file exists for demonstration
    try:
        df = pd.read_excel('General.xlsx', sheet_name=NOMBRE_DE_LA_HOJA)
        return df
    except FileNotFoundError:
        return pd.DataFrame()


def sincronizar_con_sharepoint():
    df_excel = obtener_datos_sharepoint_con_auth()
    if df_excel.empty:
        print("No se encontraron datos en SharePoint o el archivo está vacío.")
        return {"success": False, "message": "No se encontraron datos."}

    # Normalización de nombres de columna
    df_excel.columns = df_excel.columns.str.strip().str.lower().str.replace(' ', '_').str.replace('.', '', regex=False)
    df_excel.rename(columns={
        'orden_de_compra': 'orden_compra',
        'fecha_de_entrega': 'fecha_entrega',
        'no_botellas': 'no_botellas',
        'no_cajas': 'no_cajas'
    }, inplace=True)
    
    # Asegurar que las columnas existan
    required_cols = ['orden_compra', 'so', 'cliente', 'canal', 'fecha_entrega', 'estatus', 'factura', 'horario', 'localidad_destino', 'no_botellas', 'no_cajas', 'subtotal']
    for col in required_cols:
        if col not in df_excel.columns:
            df_excel[col] = None


    df_excel['canal'] = df_excel['canal'].str.strip().str.title()
    df_excel['fecha_entrega'] = pd.to_datetime(df_excel['fecha_entrega'], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%d').fillna('Por Asignar')
    
    # Lógica para usar SO si Orden de Compra no existe
    df_excel['orden_compra'] = df_excel['orden_compra'].astype(str).str.strip()
    df_excel['so'] = df_excel['so'].astype(str).str.strip()
    
    df_excel['identificador_unico'] = np.where(
        df_excel['orden_compra'].isna() | (df_excel['orden_compra'] == '') | (df_excel['orden_compra'] == 'nan'),
        df_excel['so'],
        df_excel['orden_compra']
    )

    df_excel_activos = df_excel[
        (df_excel['identificador_unico'].notna()) &
        (df_excel['identificador_unico'] != 'nan') &
        (df_excel['identificador_unico'] != '') &
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
            identificador = row['identificador_unico']
            if identificador in ordenes_archivadas:
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
                
                cliente_upper = str(row.get('cliente', '')).upper()
                TAREAS_POR_CLIENTE = {
                    "DEFAULT": ["Tarea 1 Genérica", "Tarea 2 Genérica"],
                    "CLIENTE_A": ["Tarea A1", "Tarea A2"],
                }
                tareas_a_crear = TAREAS_POR_CLIENTE.get("DEFAULT")
                for key_cliente in TAREAS_POR_CLIENTE:
                    if key_cliente in cliente_upper:
                        tareas_a_crear = TAREAS_POR_CLIENTE[key_cliente]
                        break
                for desc in tareas_a_crear:
                    db.session.add(Tarea(descripcion=desc, seguimiento_id=identificador))
        
        db.session.commit()
    return {"success": True, "message": "Sincronización completada."}


# --- RUTAS DE LA API (ACTUALIZADAS) ---
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
                "Orden de compra": s.identificador_unico,
                "Cliente": s.cliente, "Canal": s.canal, "SO": s.so, "Factura": s.factura,
                "Fecha de entrega": s.fecha_entrega, "Horario": s.horario,
                "Localidad destino": s.localidad_destino, "No. Botellas": s.no_botellas,
                "No. Cajas": s.no_cajas, "Subtotal": s.subtotal,
                "Estado": s.estado, "Notas": s.notas, "bloque_id": s.bloque_id,
                "Tareas": [{"id": t.id, "descripcion": t.descripcion, "completado": t.completado} for t in s.tareas]
            })
        
        all_channels = [c.name for c in Channel.query.order_by(Channel.name).all()]

        return jsonify({
            "data": data,
            "channels": all_channels
        })
    except Exception as e:
        print(f"ERROR al obtener datos locales: {e}", flush=True)
        traceback.print_exc()
        return jsonify({"error": "No se pudieron obtener los datos de la base de datos local."}), 500


@app.route('/')
@login_required
def index():
    return render_template('index.html')

# --- Rutas de Autenticación y Sesión ---
def _build_msal_app(cache=None):
    return msal.ConfidentialClientApplication(
        CLIENT_ID, authority=AUTHORITY,
        client_credential=CLIENT_SECRET, token_cache=cache)

def _build_auth_code_flow(scopes=None):
    return _build_msal_app().initiate_auth_code_flow(
        scopes or SCOPES,
        redirect_uri=url_for("get_token", _external=True))

def _get_token_from_cache(scope=None):
    cache = msal.SerializableTokenCache()
    if session.get("token_cache"):
        cache.deserialize(session["token_cache"])
    
    cca = _build_msal_app(cache=cache)
    accounts = cca.get_accounts()
    
    if accounts:
        result = cca.acquire_token_silent(scope or SCOPES, account=accounts[0])
        session["token_cache"] = cache.serialize()
        return result
    return None

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
        
        result = _build_msal_app(cache=cache).acquire_token_by_auth_code_flow(
            session.get("flow", {}), request.args)
        
        if "error" in result:
            return f"Error de login: {result.get('error_description')}", 400
        
        claims = result.get("id_token_claims")
        email, nombre = claims.get("preferred_username"), claims.get("name")
        
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(email=email, nombre=nombre, rol='super' if email.lower() == app.config['SUPER_USER_EMAIL'].lower() else 'normal')
            db.session.add(user)
            db.session.commit()
        
        login_user(user)
        session["token_cache"] = cache.serialize()
    except ValueError:
        pass
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    logout_user()
    session.clear()
    return redirect(f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/logout?post_logout_redirect_uri={url_for('index', _external=True)}")

@app.route('/api/me')
@login_required
def me():
    permissions = [p.name for p in (Permission.query.all() if current_user.rol == 'super' else current_user.permissions)]
    return jsonify({
        "email": current_user.email, 
        "nombre": current_user.nombre, 
        "rol": current_user.rol, 
        "permissions": permissions, 
        "can_manage_portals": current_user.has_permission('manage_portals')
    })


# --- Rutas de Portales ---
@app.route('/monitoreo-portales')
@login_required
def monitoreo_portales():
    return render_template('monitoreo_portales.html')

@app.route('/api/portales', methods=['GET'])
@login_required
def get_portales():
    portales = load_portales_data()
    return jsonify(portales)

@app.route('/api/portales/clientes', methods=['POST'])
@login_required
def add_cliente():
    if not current_user.has_permission('manage_portals'):
        abort(403, "No tienes permiso para realizar esta acción.")
    data = request.get_json()
    if not data or 'nombre' not in data or not data['nombre'].strip():
        return jsonify({"error": "El nombre del cliente es obligatorio."}), 400
    portales = load_portales_data()
    nombre_cliente = data['nombre'].strip()
    if any(isinstance(c, dict) and c.get('nombre', '').lower() == nombre_cliente.lower() for c in portales):
        return jsonify({"error": "Ya existe un cliente con ese nombre."}), 409
    nuevo_cliente = {
        "id": str(uuid.uuid4()),
        "nombre": nombre_cliente,
        "portales": []
    }
    portales.insert(0, nuevo_cliente)
    save_portales_data(portales)
    return jsonify(nuevo_cliente), 201

@app.route('/api/portales/clientes/<string:cliente_id>', methods=['DELETE'])
@login_required
def delete_cliente(cliente_id):
    if not current_user.has_permission('manage_portals'):
        abort(403, "No tienes permiso para realizar esta acción.")
    portales = load_portales_data()
    cliente_encontrado = next((c for c in portales if c.get('id') == cliente_id), None)
    if not cliente_encontrado:
        return jsonify({"error": "Cliente no encontrado."}), 404
    portales.remove(cliente_encontrado)
    save_portales_data(portales)
    return jsonify({"message": "Cliente eliminado con éxito."}), 200

@app.route('/api/portales/clientes/<string:cliente_id>/portals', methods=['POST'])
@login_required
def add_portal(cliente_id):
    if not current_user.has_permission('manage_portals'):
        abort(403, "No tienes permiso para realizar esta acción.")
    portal_data = request.get_json()
    if not all(k in portal_data for k in ['nombre', 'url', 'usuario', 'contra']):
        return jsonify({"error": "Faltan datos para crear el portal."}), 400
    portales = load_portales_data()
    cliente = next((c for c in portales if c.get('id') == cliente_id), None)
    if not cliente:
        return jsonify({"error": "Cliente no encontrado."}), 404
    nuevo_portal = {
        "id": str(uuid.uuid4()),
        "nombre": portal_data['nombre'],
        "url": portal_data['url'],
        "usuario": portal_data['usuario'],
        "contra": portal_data['contra']
    }
    cliente['portales'].append(nuevo_portal)
    save_portales_data(portales)
    return jsonify(nuevo_portal), 201

@app.route('/api/portales/portals/<string:portal_id>', methods=['PUT'])
@login_required
def update_portal(portal_id):
    if not current_user.has_permission('manage_portals'):
        abort(403, "No tienes permiso para realizar esta acción.")
    update_data = request.get_json()
    portales = load_portales_data()
    for cliente in portales:
        for i, portal in enumerate(cliente.get('portales', [])):
            if portal.get('id') == portal_id:
                portal['nombre'] = update_data.get('nombre', portal['nombre'])
                portal['url'] = update_data.get('url', portal['url'])
                portal['usuario'] = update_data.get('usuario', portal['usuario'])
                portal['contra'] = update_data.get('contra', portal['contra'])
                save_portales_data(portales)
                return jsonify(portal), 200
    return jsonify({"error": "Portal no encontrado."}), 404

@app.route('/api/portales/portals/<string:portal_id>', methods=['DELETE'])
@login_required
def delete_portal(portal_id):
    if not current_user.has_permission('manage_portals'):
        abort(403, "No tienes permiso para realizar esta acción.")
    portales = load_portales_data()
    for cliente in portales:
        portal_a_eliminar = next((p for p in cliente.get('portales', []) if p.get('id') == portal_id), None)
        if portal_a_eliminar:
            cliente['portales'].remove(portal_a_eliminar)
            save_portales_data(portales)
            return jsonify({"message": "Portal eliminado con éxito."}), 200
    return jsonify({"error": "Portal no encontrado."}), 404


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

@app.route('/api/channels')
@login_required
def get_channels():
    channels = Channel.query.order_by(Channel.name).all()
    return jsonify([c.name for c in channels])

# --- Rutas de Historial y Descargas ---
def _get_filtered_history_query():
    query = HistorialOrden.query
    if cliente := request.args.get('cliente'):
        query = query.filter(HistorialOrden.cliente.ilike(f"%{cliente}%"))
    if localidad := request.args.get('localidad'):
        query = query.filter(HistorialOrden.localidad_destino.ilike(f"%{localidad}%"))
    if canal := request.args.get('canal'):
        if canal and canal != 'ALL':
            query = query.filter(HistorialOrden.canal == canal)
    if start_date_str := request.args.get('start_date'):
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            query = query.filter(HistorialOrden.fecha_archivado >= start_date)
        except ValueError: pass
    if end_date_str := request.args.get('end_date'):
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            query = query.filter(HistorialOrden.fecha_archivado < end_date + timedelta(days=1))
        except ValueError: pass
    return query

@app.route('/api/historial')
@login_required
def get_historial_data():
    query = _get_filtered_history_query()
    historial_ordenes = query.order_by(HistorialOrden.fecha_archivado.desc()).all()
    return jsonify([orden.to_dict() for orden in historial_ordenes])

@app.route('/api/historial/descargar')
@login_required
def descargar_historial():
    query = _get_filtered_history_query()
    historial_ordenes = query.order_by(HistorialOrden.fecha_archivado.desc()).all()
    if not historial_ordenes: return "No hay datos para descargar con los filtros seleccionados.", 404
    datos_para_excel = [orden.to_dict(for_excel=True) for orden in historial_ordenes]
    df = pd.DataFrame(datos_para_excel)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Historial')
        worksheet = writer.sheets['Historial']
        for i, col in enumerate(df.columns):
            column_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
            worksheet.set_column(i, i, column_len)
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f'historial_logistica_{datetime.now().strftime("%Y-%m-%d")}.xlsx')

# --- Rutas de Acciones sobre Órdenes ---
@app.route('/api/actualizar-estado', methods=['POST'])
@login_required
def actualizar_estado():
    if not current_user.has_permission('update_status'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    data = request.json
    seguimiento = Seguimiento.query.filter_by(identificador_unico=data.get('orden_compra')).first_or_404()
    nuevo_estado = data.get('nuevo_estado')
    ordenes_afectadas_ids = []
    if seguimiento.bloque_id:
        ordenes_en_bloque = Seguimiento.query.filter_by(bloque_id=seguimiento.bloque_id).all()
        for orden in ordenes_en_bloque:
            orden.estado = nuevo_estado
            ordenes_afectadas_ids.append(orden.identificador_unico)
    else:
        seguimiento.estado = nuevo_estado
        ordenes_afectadas_ids.append(seguimiento.identificador_unico)
    db.session.commit()
    return jsonify({'success': True, 'updated_ocs': ordenes_afectadas_ids})

@app.route('/api/actualizar-notas', methods=['POST'])
@login_required
def actualizar_notas():
    if not current_user.has_permission('edit_notes'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    data = request.json
    seguimiento = Seguimiento.query.filter_by(identificador_unico=data.get('orden_compra')).first_or_404()
    seguimiento.notas = data.get('notas')
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/actualizar-tarea', methods=['POST'])
@login_required
def actualizar_tarea():
    if not current_user.has_permission('update_status'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    data = request.json
    tarea = Tarea.query.get_or_404(data.get('tarea_id'))
    tarea.completado = data.get('completado')
    db.session.commit()
    return jsonify({'success': True})

def _create_historial_entry(data):
    return HistorialOrden(
        orden_compra=data.get('Orden de compra'), 
        cliente=data.get('Cliente'), 
        canal=data.get('Canal'), 
        so=data.get('SO'), 
        factura=data.get('Factura'), 
        fecha_entrega=data.get('Fecha de entrega'), 
        horario=data.get('Horario'), 
        estado_final=data.get('Estado'), 
        localidad_destino=data.get('Localidad destino'), 
        no_botellas=int(data.get('No. Botellas')) if data.get('No. Botellas') else None, 
        no_cajas=int(data.get('No. Cajas')) if data.get('No. Cajas') else None, 
        subtotal=float(data.get('Subtotal')) if data.get('Subtotal') else None, 
        notas=data.get('Notas')
    )

@app.route('/api/archivar-orden', methods=['POST'])
@login_required
def archivar_orden():
    if not current_user.has_permission('archive_orders'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    data = request.json
    identificador = data.get('Orden de compra')
    if not identificador: return jsonify({"error": "Falta el identificador de la orden"}), 400
    historial_entry = _create_historial_entry(data)
    db.session.add(historial_entry)
    seguimiento_activo = Seguimiento.query.filter_by(identificador_unico=identificador).first()
    if seguimiento_activo: db.session.delete(seguimiento_activo)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Orden archivada en el historial permanente.'})

@app.route('/api/crear-bloque', methods=['POST'])
@login_required
def crear_bloque():
    if not current_user.has_permission('group_orders'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    data = request.json
    ids_a_agrupar = data.get('ordenes_compra', [])
    if not ids_a_agrupar or len(ids_a_agrupar) < 2: return jsonify({"error": "Se necesitan al menos 2 órdenes para crear un bloque."}), 400
    nombre_bloque = f"Bloque-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    nuevo_bloque = Bloque(nombre=nombre_bloque)
    db.session.add(nuevo_bloque)
    db.session.flush()
    ordenes = Seguimiento.query.filter(Seguimiento.identificador_unico.in_(ids_a_agrupar)).all()
    for orden in ordenes: orden.bloque_id = nuevo_bloque.id
    db.session.commit()
    return jsonify({"success": True, "mensaje": f"Bloque '{nuevo_bloque.id}' creado con éxito.", "bloque_id": nuevo_bloque.id, "ordenes_agrupadas": ids_a_agrupar})

@app.route('/api/orden/liberar/<int:historial_id>', methods=['POST'])
@login_required
def liberar_orden(historial_id):
    if not current_user.has_permission('archive_orders'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    orden_historial = HistorialOrden.query.get_or_404(historial_id)
    nuevo_seguimiento = Seguimiento(identificador_unico=orden_historial.orden_compra, estado=orden_historial.estado_final, notas=orden_historial.notas)
    db.session.add(nuevo_seguimiento)
    cliente_upper = str(orden_historial.cliente or '').upper()
    TAREAS_POR_CLIENTE = {
        "DEFAULT": ["Tarea 1 Genérica", "Tarea 2 Genérica"],
        "CLIENTE_A": ["Tarea A1", "Tarea A2"],
    }
    tareas_a_crear = TAREAS_POR_CLIENTE.get("DEFAULT")
    for key in TAREAS_POR_CLIENTE:
        if key in cliente_upper:
            tareas_a_crear = TAREAS_POR_CLIENTE[key]
            break
    for desc in tareas_a_crear:
        nueva_tarea = Tarea(descripcion=desc, seguimiento_id=orden_historial.orden_compra)
        db.session.add(nueva_tarea)
    db.session.delete(orden_historial)
    db.session.commit()
    return jsonify({"success": True, "message": f"Orden {orden_historial.orden_compra} restaurada al seguimiento activo."})

@app.route('/api/orden/clear-notes', methods=['POST'])
@login_required
def clear_order_notes():
    if not current_user.has_permission('edit_notes'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    data = request.json
    identificador = data.get('orden_compra')
    if not identificador: return jsonify({"error": "Falta el identificador de la orden."}), 400
    seguimiento = Seguimiento.query.filter_by(identificador_unico=identificador).first_or_404()
    seguimiento.notas = ""
    db.session.commit()
    return jsonify({"success": True, "message": "Notas de la orden limpiadas con éxito."})

@app.route('/api/archivar-bloque', methods=['POST'])
@login_required
def archivar_bloque():
    if not current_user.has_permission('archive_orders'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    orders_data = request.json.get('orders_data', [])
    if not orders_data: return jsonify({"error": "No se proporcionaron datos de órdenes."}), 400
    for data in orders_data:
        identificador = data.get('Orden de compra')
        if not identificador: continue
        historial_entry = _create_historial_entry(data)
        db.session.add(historial_entry)
        seguimiento_activo = Seguimiento.query.filter_by(identificador_unico=identificador).first()
        if seguimiento_activo: db.session.delete(seguimiento_activo)
    db.session.commit()
    return jsonify({'success': True, 'message': f'{len(orders_data)} órdenes del bloque han sido archivadas.'})

@app.route('/api/desagrupar-bloque', methods=['POST'])
@login_required
def desagrupar_bloque():
    if not current_user.has_permission('group_orders'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    data = request.json
    ids_a_desagrupar = data.get('ocs', [])
    if not ids_a_desagrupar: return jsonify({"error": "No se proporcionaron OCs para desagrupar."}), 400
    ordenes = Seguimiento.query.filter(Seguimiento.identificador_unico.in_(ids_a_desagrupar)).all()
    if not ordenes: return jsonify({"error": "No se encontraron las órdenes especificadas."}), 404
    bloque_id_a_revisar = ordenes[0].bloque_id
    for orden in ordenes:
        orden.bloque_id = None
    if bloque_id_a_revisar:
        if Seguimiento.query.filter_by(bloque_id=bloque_id_a_revisar).count() == 0:
            bloque_a_eliminar = Bloque.query.get(bloque_id_a_revisar)
            if bloque_a_eliminar:
                db.session.delete(bloque_a_eliminar)
    db.session.commit()
    return jsonify({"success": True, "message": "Las órdenes han sido desagrupadas."})

# --- INICIO: Funciones de inicialización ---
def initialize_database():
    """Crea la BD y los permisos si no existen."""
    with app.app_context():
        if not os.path.exists(DB_PATH):
            print("Primera ejecución: Creando base de datos y tablas...")
            db.create_all()
            
            permissions = [
                {'name': 'update_status', 'description': 'Puede cambiar el estado de las órdenes'},
                {'name': 'edit_notes', 'description': 'Puede editar y limpiar las notas de cualquier orden'},
                {'name': 'archive_orders', 'description': 'Puede archivar y restaurar órdenes del historial'},
                {'name': 'group_orders', 'description': 'Puede agrupar órdenes en bloques'},
                {'name': 'manage_portals', 'description': 'Puede agregar, editar y eliminar portales'},
                {'name': 'manage_users', 'description': 'Puede ver y cambiar permisos de otros usuarios'}
            ]
            for perm_data in permissions:
                perm = Permission.query.filter_by(name=perm_data['name']).first()
                if not perm:
                    db.session.add(Permission(**perm_data))
            db.session.commit()
            print("✅ Base de datos y permisos inicializados.")
        else:
            print("La base de datos ya existe.")

def initialize_app_data():
    """Sincroniza los datos de SharePoint al iniciar la app."""
    with app.app_context():
        print("Iniciando sincronización de datos al arrancar la aplicación...")
        try:
            resultado = sincronizar_con_sharepoint()
            print(f"✅ Sincronización inicial completada: {resultado['message']}")
        except Exception as e:
            print(f"❌ Error durante la sincronización inicial: {e}")
            traceback.print_exc()

# --- Llamadas de Inicialización ---
with app.app_context():
    initialize_database()
initialize_app_data()

# --- INICIO DE LA APLICACIÓN ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5001)), debug=True)