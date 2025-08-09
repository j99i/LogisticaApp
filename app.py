import os
import json
import uuid
import traceback # Importar para un mejor log de errores
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
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
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
        if self.rol == 'super':
            return True
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
    orden_compra = db.Column(db.String(100), unique=True, nullable=False)
    estado = db.Column(db.String(100), nullable=False, default='Pendiente')
    notas = db.Column(db.Text, nullable=True)
    archivada = db.Column(db.Boolean, default=False)
    tareas = db.relationship('Tarea', backref='seguimiento', lazy=True, cascade="all, delete-orphan")
    bloque_id = db.Column(db.Integer, db.ForeignKey('bloque.id'), nullable=True)
class Tarea(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descripcion = db.Column(db.String(200), nullable=False)
    completado = db.Column(db.Boolean, default=False)
    seguimiento_oc = db.Column(db.String(100), db.ForeignKey('seguimiento.orden_compra'), nullable=False)
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

# --- COMANDOS DE TERMINAL ---
@app.cli.command('create-db')
def create_db_command():
    with app.app_context():
        db.create_all()
    print('✅ Base de datos y tablas creadas.')

@app.cli.command('init-permissions')
def init_permissions_command():
    permissions = [
        {'name': 'update_status', 'description': 'Puede cambiar el estado de las órdenes'},
        {'name': 'edit_notes', 'description': 'Puede editar y limpiar las notas de cualquier orden'},
        {'name': 'archive_orders', 'description': 'Puede archivar y restaurar órdenes del historial'},
        {'name': 'group_orders', 'description': 'Puede agrupar órdenes en bloques'},
        {'name': 'manage_portals', 'description': 'Puede agregar, editar y eliminar portales'},
        {'name': 'manage_users', 'description': 'Puede ver y cambiar permisos de otros usuarios'}
    ]
    with app.app_context():
        for perm_data in permissions:
            perm = Permission.query.filter_by(name=perm_data['name']).first()
            if not perm:
                db.session.add(Permission(**perm_data))
            else:
                perm.description = perm_data['description']
        db.session.commit()
    print('✅ Permisos inicializados y actualizados con éxito en español.')

@app.cli.command('assign-role')
def assign_role_command(email, role):
    with app.app_context():
        if role not in ['super', 'admin', 'normal']:
            print(f"Error: El rol '{role}' no es válido.")
            return
        user = User.query.filter_by(email=email).first()
        if not user:
            print(f"Error: No se encontró al usuario con el email '{email}'.")
            return
        user.rol = role
        db.session.commit()
        print(f"✅ Rol '{role}' asignado exitosamente a {email}.")

# --- RUTAS Y LÓGICA DE LA APLICACIÓN ---
TAREAS_POR_CLIENTE = {
    "WALMART": ["Enviar confirmación de cita", "Subir templates", "Preguntar status en WhatsApp (Ruta)", "Pedir evidencia fotográfica (Entrega)"],
    "CHEDRAUI": ["Solicitud de cita", "Generar templates", "Enviar correo de aviso", "Confirmar cita por correo", "Preguntar status en WhatsApp (Ruta)", "Pedir evidencia fotográfica (Entrega)"],
    "DEFAULT": ["Confirmar cita", "Preguntar status en WhatsApp (Ruta)", "Pedir evidencia fotográfica (Entrega)"]
}
def _build_msal_app(cache=None):
    return msal.ConfidentialClientApplication(CLIENT_ID, authority=AUTHORITY, client_credential=CLIENT_SECRET, token_cache=cache)
def _get_token_from_cache():
    cache = msal.SerializableTokenCache()
    if session.get("token_cache"):
        cache.deserialize(session["token_cache"])
    return cache
def obtener_datos_sharepoint_con_auth():
    print("⏳ Iniciando obtención de datos de SharePoint...")
    token_cache = _get_token_from_cache()
    msal_app = _build_msal_app(cache=token_cache)
    accounts = msal_app.get_accounts()
    if not accounts:
        raise Exception("No se encontró la cuenta en caché. Por favor, inicie sesión de nuevo.")
    token_response = msal_app.acquire_token_silent(SCOPES, account=accounts[0])
    if not token_response:
        raise Exception("No se pudo obtener el token de acceso de forma silenciosa.")
    session["token_cache"] = token_cache.serialize()
    headers = {'Authorization': f'Bearer {token_response["access_token"]}'}
    base64_bytes = base64.b64encode(SHARING_URL.encode('utf-8'))
    base64_string = base64_bytes.decode('utf-8')
    encoded_url = "u!" + base64_string.replace('=', '').replace('/', '_').replace('+', '-')
    api_url_item = f"https://graph.microsoft.com/v1.0/shares/{encoded_url}/driveItem"
    item_response = requests.get(api_url_item, headers=headers)
    item_response.raise_for_status()
    download_url = item_response.json().get('@microsoft.graph.downloadUrl')
    if not download_url:
        raise Exception("No se pudo obtener la URL de descarga del archivo.")
    file_response = requests.get(download_url)
    file_response.raise_for_status()
    excel_data = io.BytesIO(file_response.content)
    df = pd.read_excel(excel_data, sheet_name=NOMBRE_DE_LA_HOJA, dtype=str)
    print("✅ Archivo de Excel leído.")
    df.columns = df.columns.str.strip()
    return df
def sincronizar_y_obtener_datos_completos(canal_filtro=None):
    df_excel = obtener_datos_sharepoint_con_auth()
    df_excel['Canal'] = df_excel['Canal'].str.strip().str.title()
    df_excel['Fecha de entrega'] = pd.to_datetime(df_excel['Fecha de entrega'], dayfirst=True, errors='coerce')
    df_excel['Fecha de entrega'] = df_excel['Fecha de entrega'].dt.strftime('%Y-%m-%d').fillna('Por Asignar')
    df_excel_activos = df_excel[df_excel['Estatus'].fillna('').str.strip() == ''].copy()
    with app.app_context():
        all_unique_channels = sorted(df_excel['Canal'].dropna().unique().tolist())
        existing_channels = [c.name for c in Channel.query.all()]
        for channel_name in all_unique_channels:
            if channel_name not in existing_channels:
                db.session.add(Channel(name=channel_name))
        ordenes_ya_archivadas = {h.orden_compra for h in HistorialOrden.query.all()}
        for _, row in df_excel_activos.iterrows():
            oc_str = str(row.get('Orden de compra'))
            if oc_str and oc_str != 'nan' and oc_str not in ordenes_ya_archivadas:
                if not Seguimiento.query.filter_by(orden_compra=oc_str).first():
                    nuevo_seguimiento = Seguimiento(orden_compra=oc_str)
                    db.session.add(nuevo_seguimiento)
                    cliente_upper = str(row.get('Cliente', '')).upper()
                    tareas_a_crear = TAREAS_POR_CLIENTE.get("DEFAULT")
                    for key in TAREAS_POR_CLIENTE:
                        if key in cliente_upper:
                            tareas_a_crear = TAREAS_POR_CLIENTE[key]
                            break
                    for desc in tareas_a_crear:
                        db.session.add(Tarea(descripcion=desc, seguimiento_oc=oc_str))
        db.session.commit()
        seguimientos_activos = Seguimiento.query.options(db.joinedload(Seguimiento.tareas)).all()
        if not seguimientos_activos:
            return pd.DataFrame(), all_unique_channels
        datos_finales = []
        for s in seguimientos_activos:
            datos_finales.append({
                'Orden de compra': s.orden_compra, 'Estado': s.estado, 'Notas': s.notas,
                'Archivada': s.archivada, 'bloque_id': s.bloque_id,
                'Tareas': [{"id": t.id, "descripcion": t.descripcion, "completado": t.completado} for t in s.tareas]
            })
        df_final = pd.DataFrame(datos_finales)
        df_excel_activos = df_excel_activos.dropna(subset=['Orden de compra'])
        df_final = pd.merge(df_final, df_excel_activos, on='Orden de compra', how='left')
        if canal_filtro and canal_filtro.upper() != 'ALL':
            df_final = df_final[df_final['Canal'].fillna('').str.strip().str.title() == canal_filtro.title()]
    df_final = df_final.replace({np.nan: None, pd.NaT: None})
    return df_final, all_unique_channels
@app.route('/')
@login_required
def index():
    return render_template('index.html')
@app.route('/login')
def login():
    session["flow"] = _build_auth_code_flow()
    return redirect(session["flow"]["auth_uri"])
@app.route(REDIRECT_PATH)
def get_token():
    try:
        cache = _get_token_from_cache()
        result = _build_msal_app(cache=cache).acquire_token_by_auth_code_flow(session.get("flow", {}), request.args)
        if "error" in result: return f"Error de login: {result.get('error_description')}", 400
        claims = result.get("id_token_claims")
        email, nombre = claims.get("preferred_username"), claims.get("name")
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(email=email, nombre=nombre, rol='super' if email.lower() == app.config['SUPER_USER_EMAIL'].lower() else 'normal')
            db.session.add(user)
            db.session.commit()
        login_user(user)
        session["token_cache"] = cache.serialize()
    except ValueError: pass
    return redirect(url_for('index'))
@app.route('/logout')
def logout():
    logout_user()
    session.clear()
    return redirect(f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/logout?post_logout_redirect_uri={url_for('index', _external=True)}")
def _build_auth_code_flow(scopes=None): return _build_msal_app().initiate_auth_code_flow(scopes or SCOPES, redirect_uri=url_for("get_token", _external=True))
@app.route('/admin/users')
@login_required
def admin_users_page():
    if not current_user.rol == 'super': abort(403)
    return render_template('admin_users.html')
@app.route('/api/me')
@login_required
def me():
    permissions = [p.name for p in (Permission.query.all() if current_user.rol == 'super' else current_user.permissions)]
    return jsonify({"email": current_user.email, "nombre": current_user.nombre, "rol": current_user.rol, "permissions": permissions, "can_manage_portals": current_user.has_permission('manage_portals')})
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

@app.route('/api/logistica/datos')
@login_required
def get_logistica_data():
    # --- INICIO: Bloque de manejo de errores para la sincronización ---
    try:
        requested_channel = request.args.get('canal')

        # Determinar los canales disponibles para el usuario
        if current_user.rol == 'super':
            channels_for_user = [c.name for c in Channel.query.order_by(Channel.name).all()]
        else:
            channels_for_user = [c.name for c in current_user.allowed_channels]

        # Si el usuario no tiene canales, devolver una respuesta vacía
        if not channels_for_user and current_user.rol != 'super':
            return jsonify({"data": [], "channels": [], "loaded_channel": None})

        # Determinar qué canal cargar
        if requested_channel and (requested_channel in channels_for_user or (requested_channel == 'ALL' and current_user.rol == 'super')):
            channel_to_load = requested_channel
        elif current_user.rol == 'super':
            channel_to_load = 'ALL'
        else:
            channel_to_load = channels_for_user[0]

        df_filtrado, _ = sincronizar_y_obtener_datos_completos(channel_to_load)
        
        return jsonify({
            "data": df_filtrado.to_dict('records'),
            "channels": channels_for_user,
            "loaded_channel": channel_to_load
        })
    except Exception as e:
        # Imprime el error detallado en los logs del servidor (visible en Render)
        print(f"ERROR CRÍTICO al sincronizar con SharePoint: {e}", flush=True)
        traceback.print_exc()
        # Devuelve una respuesta de error al frontend
        return jsonify({"error": "No se pudo sincronizar con la fuente de datos (SharePoint). Revise los logs del servidor para más detalles."}), 500
    # --- FIN: Bloque de manejo de errores ---

@app.route('/api/channels')
@login_required
def get_channels():
    channels = Channel.query.order_by(Channel.name).all()
    return jsonify([c.name for c in channels])
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
@app.route('/api/actualizar-estado', methods=['POST'])
@login_required
def actualizar_estado():
    if not current_user.has_permission('update_status'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    data = request.json
    orden_compra = data.get('orden_compra')
    nuevo_estado = data.get('nuevo_estado')
    seguimiento = Seguimiento.query.filter_by(orden_compra=orden_compra).first_or_404()
    ordenes_afectadas_ocs = []
    if seguimiento.bloque_id:
        ordenes_en_bloque = Seguimiento.query.filter_by(bloque_id=seguimiento.bloque_id).all()
        for orden in ordenes_en_bloque:
            orden.estado = nuevo_estado
            ordenes_afectadas_ocs.append(orden.orden_compra)
    else:
        seguimiento.estado = nuevo_estado
        ordenes_afectadas_ocs.append(seguimiento.orden_compra)
    db.session.commit()
    return jsonify({'success': True, 'updated_ocs': ordenes_afectadas_ocs})
@app.route('/api/actualizar-notas', methods=['POST'])
@login_required
def actualizar_notas():
    if not current_user.has_permission('edit_notes'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    data = request.json
    seguimiento = Seguimiento.query.filter_by(orden_compra=data.get('orden_compra')).first_or_404()
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
    return HistorialOrden(orden_compra=data.get('Orden de compra'), cliente=data.get('Cliente'), canal=data.get('Canal'), so=data.get('SO'), factura=data.get('Factura'), fecha_entrega=data.get('Fecha de entrega'), horario=data.get('Horario'), estado_final=data.get('Estado'), localidad_destino=data.get('Localidad destino'), no_botellas=int(data.get('No. Botellas')) if data.get('No. Botellas') else None, no_cajas=int(data.get('No. Cajas')) if data.get('No. Cajas') else None, subtotal=float(data.get('Subtotal')) if data.get('Subtotal') else None, notas=data.get('Notas'))
@app.route('/api/archivar-orden', methods=['POST'])
@login_required
def archivar_orden():
    if not current_user.has_permission('archive_orders'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    data = request.json
    oc = data.get('Orden de compra')
    if not oc: return jsonify({"error": "Falta la orden de compra"}), 400
    historial_entry = _create_historial_entry(data)
    db.session.add(historial_entry)
    seguimiento_activo = Seguimiento.query.filter_by(orden_compra=oc).first()
    if seguimiento_activo: db.session.delete(seguimiento_activo)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Orden archivada en el historial permanente.'})
@app.route('/api/crear-bloque', methods=['POST'])
@login_required
def crear_bloque():
    if not current_user.has_permission('group_orders'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    data = request.json
    ocs_a_agrupar = data.get('ordenes_compra', [])
    if not ocs_a_agrupar or len(ocs_a_agrupar) < 2: return jsonify({"error": "Se necesitan al menos 2 órdenes para crear un bloque."}), 400
    nombre_bloque = f"Bloque-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    nuevo_bloque = Bloque(nombre=nombre_bloque)
    db.session.add(nuevo_bloque)
    db.session.flush()
    ordenes = Seguimiento.query.filter(Seguimiento.orden_compra.in_(ocs_a_agrupar)).all()
    for orden in ordenes: orden.bloque_id = nuevo_bloque.id
    db.session.commit()
    return jsonify({"success": True, "mensaje": f"Bloque '{nuevo_bloque.id}' creado con éxito.", "bloque_id": nuevo_bloque.id, "ordenes_agrupadas": ocs_a_agrupar})
@app.route('/api/orden/liberar/<int:historial_id>', methods=['POST'])
@login_required
def liberar_orden(historial_id):
    if not current_user.has_permission('archive_orders'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    orden_historial = HistorialOrden.query.get_or_404(historial_id)
    nuevo_seguimiento = Seguimiento(orden_compra=orden_historial.orden_compra, estado=orden_historial.estado_final, notas=orden_historial.notas)
    db.session.add(nuevo_seguimiento)
    cliente_upper = str(orden_historial.cliente or '').upper()
    tareas_a_crear = TAREAS_POR_CLIENTE.get("DEFAULT")
    for key in TAREAS_POR_CLIENTE:
        if key in cliente_upper:
            tareas_a_crear = TAREAS_POR_CLIENTE[key]
            break
    for desc in tareas_a_crear:
        nueva_tarea = Tarea(descripcion=desc, seguimiento_oc=orden_historial.orden_compra)
        db.session.add(nueva_tarea)
    db.session.delete(orden_historial)
    db.session.commit()
    return jsonify({"success": True, "message": f"Orden {orden_historial.orden_compra} restaurada al seguimiento activo."})
@app.route('/api/orden/clear-notes', methods=['POST'])
@login_required
def clear_order_notes():
    if not current_user.has_permission('edit_notes'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    data = request.json
    oc = data.get('orden_compra')
    if not oc: return jsonify({"error": "Falta la orden de compra."}), 400
    seguimiento = Seguimiento.query.filter_by(orden_compra=oc).first_or_404()
    seguimiento.notas = ""
    db.session.commit()
    return jsonify({"success": True, "message": "Notas de la orden limpiadas con éxito."})
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
@app.route('/api/archivar-bloque', methods=['POST'])
@login_required
def archivar_bloque():
    if not current_user.has_permission('archive_orders'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    orders_data = request.json.get('orders_data', [])
    if not orders_data: return jsonify({"error": "No se proporcionaron datos de órdenes."}), 400
    for data in orders_data:
        oc = data.get('Orden de compra')
        if not oc: continue
        historial_entry = _create_historial_entry(data)
        db.session.add(historial_entry)
        seguimiento_activo = Seguimiento.query.filter_by(orden_compra=oc).first()
        if seguimiento_activo: db.session.delete(seguimiento_activo)
    db.session.commit()
    return jsonify({'success': True, 'message': f'{len(orders_data)} órdenes del bloque han sido archivadas.'})
@app.route('/api/desagrupar-bloque', methods=['POST'])
@login_required
def desagrupar_bloque():
    if not current_user.has_permission('group_orders'): return jsonify({"error": "No tienes permiso para esta acción."}), 403
    data = request.json
    ocs_a_desagrupar = data.get('ocs', [])
    if not ocs_a_desagrupar: return jsonify({"error": "No se proporcionaron OCs para desagrupar."}), 400
    ordenes = Seguimiento.query.filter(Seguimiento.orden_compra.in_(ocs_a_desagrupar)).all()
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

# --- INICIO: Función de inicialización automática ---
def initialize_database():
    """Crea la BD y los permisos si no existen."""
    if not os.path.exists(DB_PATH):
        print("Primera ejecución: Inicializando base de datos...")
        with app.app_context():
            db.create_all()
            
            # Lógica para inicializar permisos
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

# --- Llamada a la función de inicialización ---
initialize_database()

# --- INICIO DE LA APLICACIÓN ---
if __name__ == '__main__':
    # Este bloque solo se ejecuta en desarrollo local
    app.run(debug=True, port=5001)
