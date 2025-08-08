document.addEventListener('DOMContentLoaded', () => {

    // --- STATE MANAGEMENT ---
    // Centralized state for the entire module
    const state = {
        clients: [],
        canManage: false,
        isLoading: true,
        filterText: '',
        editingContext: null, // { type: 'client'/'portal', id: '...' }
    };

    // --- DOM ELEMENTS ---
    const clientCardsContainer = document.getElementById('clientCardsContainer');
    const searchInput = document.getElementById('searchInput');
    const headerActions = document.getElementById('header-actions');
    
    // Modal elements
    const formModalEl = document.getElementById('formModal');
    const formModal = new bootstrap.Modal(formModalEl);
    const formModalTitle = document.getElementById('formModalTitle');
    const formModalBody = document.getElementById('formModalBody');
    const formModalSaveBtn = document.getElementById('formModalSaveBtn');
    const mainForm = document.getElementById('main-form');

    // Toast elements
    const toastEl = document.getElementById('notificationToast');
    const toast = new bootstrap.Toast(toastEl, { delay: 3000 });
    const toastBody = document.getElementById('toast-body-content');
    const toastIcon = document.getElementById('toast-icon');
    const toastTitle = document.getElementById('toast-title');

    // --- API HELPERS ---
    // A namespace for all API communication
    const api = {
        async fetch(url, options = {}) {
            try {
                const response = await fetch(url, {
                    headers: { 'Content-Type': 'application/json', ...options.headers },
                    ...options,
                });
                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({ error: 'Error de comunicación con el servidor.' }));
                    throw new Error(errorData.error || `Error ${response.status}`);
                }
                if (response.status === 204 || response.status === 201 && !options.body) return null; // No content or created with no body
                return response.json();
            } catch (error) {
                showToast(error.message, true);
                throw error; // Re-throw to handle in calling function
            }
        },
        getMe: () => api.fetch('/api/me'),
        getPortals: () => api.fetch('/api/portales'),
        addClient: (name) => api.fetch('/api/portales/clientes', { method: 'POST', body: JSON.stringify({ nombre: name }) }),
        deleteClient: (clientId) => api.fetch(`/api/portales/clientes/${clientId}`, { method: 'DELETE' }),
        addPortal: (clientId, portalData) => api.fetch(`/api/portales/clientes/${clientId}/portals`, { method: 'POST', body: JSON.stringify(portalData) }),
        updatePortal: (portalId, portalData) => api.fetch(`/api/portales/portals/${portalId}`, { method: 'PUT', body: JSON.stringify(portalData) }),
        deletePortal: (portalId) => api.fetch(`/api/portales/portals/${portalId}`, { method: 'DELETE' }),
    };

    // --- RENDER FUNCTIONS ---
    // Functions to build HTML strings from state data
    const render = () => {
        if (state.isLoading) {
            clientCardsContainer.innerHTML = `
                <div class="text-center p-5">
                    <div class="spinner-border text-primary" style="width: 3rem; height: 3rem;" role="status">
                        <span class="visually-hidden">Cargando...</span>
                    </div>
                </div>`;
            return;
        }

        // Render header buttons
        headerActions.innerHTML = `
            ${state.canManage ? '<button class="btn btn-sm btn-success" data-action="show-add-client-modal"><i class="bi bi-plus-circle me-1"></i> Agregar Cliente</button>' : ''}
            <a href="/" class="btn btn-sm btn-outline-secondary"><i class="bi bi-truck me-1"></i> Volver</a>
        `;
        
        const lowerFilter = state.filterText.toLowerCase();
        const filteredClients = state.clients.filter(client => 
            client.nombre.toLowerCase().includes(lowerFilter) || 
            client.portales.some(portal => portal.nombre.toLowerCase().includes(lowerFilter))
        );

        if (filteredClients.length === 0) {
            clientCardsContainer.innerHTML = '<div class="alert alert-secondary text-center">No se encontraron clientes o portales.</div>';
            return;
        }

        clientCardsContainer.innerHTML = filteredClients.map(renderClientCard).join('');
    };

    const renderClientCard = (client) => {
        const portalsHtml = client.portales.length > 0 
            ? client.portales.map(portal => renderPortalItem(portal, client.id)).join('')
            : '<li class="list-group-item text-muted small">Este cliente no tiene portales.</li>';

        return `
            <div class="card mb-3 shadow-sm">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <h5 class="mb-0">${client.nombre}</h5>
                    ${state.canManage ? `
                    <div class="btn-group">
                        <button class="btn btn-sm btn-success" data-action="show-add-portal-modal" data-client-id="${client.id}" title="Agregar Portal">
                            <i class="bi bi-plus-circle"></i>
                        </button>
                        <button class="btn btn-sm btn-outline-danger" data-action="delete-client" data-client-id="${client.id}" data-client-name="${client.nombre}" title="Eliminar Cliente">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>` : ''}
                </div>
                <ul class="list-group list-group-flush">${portalsHtml}</ul>
            </div>`;
    };

    const renderPortalItem = (portal) => {
        return `
            <li class="list-group-item d-flex justify-content-between align-items-center flex-wrap gap-2">
                <a href="${portal.url}" target="_blank" class="text-decoration-none fw-bold" title="Abrir portal en nueva pestaña">${portal.nombre}</a>
                <div class="d-flex align-items-center gap-2">
                    <span class="badge bg-secondary-subtle text-white-50 border border-secondary-subtle">
                        <i class="bi bi-person"></i> ${portal.usuario}
                    </span>
                    <button class="btn btn-sm btn-outline-secondary" data-action="copy-text" data-text="${portal.usuario}" title="Copiar Usuario"><i class="bi bi-clipboard"></i></button>
                    <button class="btn btn-sm btn-outline-secondary" data-action="copy-text" data-text="${portal.contra}" title="Copiar Contraseña"><i class="bi bi-key"></i></button>
                    ${state.canManage ? `
                    <div class="btn-group">
                        <button class="btn btn-sm btn-outline-info" data-action="show-edit-portal-modal" data-portal-id="${portal.id}" title="Editar Portal"><i class="bi bi-pencil-fill"></i></button>
                        <button class="btn btn-sm btn-outline-danger" data-action="delete-portal" data-portal-id="${portal.id}" data-portal-name="${portal.nombre}" title="Eliminar Portal"><i class="bi bi-trash-fill"></i></button>
                    </div>` : ''}
                </div>
            </li>`;
    };

    // --- MODAL & FORM HANDLING ---
    const openModalForClient = () => {
        state.editingContext = { type: 'client' };
        formModalTitle.textContent = 'Agregar Nuevo Cliente';
        formModalBody.innerHTML = `
            <div class="mb-3">
                <label for="clientName" class="form-label">Nombre del Cliente</label>
                <input type="text" class="form-control" id="clientName" required>
            </div>`;
        formModal.show();
    };

    const openModalForPortal = (portalId = null, clientId = null) => {
        let portal = {};
        let title = '';

        if (portalId) { // Editing existing portal
            const { foundClient, foundPortal } = findPortalById(portalId);
            if (!foundPortal) return;
            portal = foundPortal;
            clientId = foundClient.id;
            title = `Editar Portal de ${foundClient.nombre}`;
            state.editingContext = { type: 'portal', id: portalId, clientId: clientId };
        } else { // Adding new portal
            const client = state.clients.find(c => c.id === clientId);
            if (!client) return;
            title = `Agregar Portal a ${client.nombre}`;
            state.editingContext = { type: 'portal', id: null, clientId: clientId };
        }

        formModalTitle.textContent = title;
        formModalBody.innerHTML = `
            <input type="hidden" id="portalClientId" value="${clientId}">
            <div class="mb-3">
                <label for="portalNombre" class="form-label">Nombre del Portal</label>
                <input type="text" class="form-control" id="portalNombre" value="${portal.nombre || ''}" required>
            </div>
            <div class="mb-3">
                <label for="portalUrl" class="form-label">URL del Portal</label>
                <input type="url" class="form-control" id="portalUrl" value="${portal.url || ''}" required>
            </div>
            <div class="mb-3">
                <label for="portalUsuario" class="form-label">Usuario</label>
                <input type="text" class="form-control" id="portalUsuario" value="${portal.usuario || ''}" required>
            </div>
            <div class="mb-3">
                <label for="portalContra" class="form-label">Contraseña</label>
                <input type="text" class="form-control" id="portalContra" value="${portal.contra || ''}" required>
            </div>`;
        formModal.show();
    };

    const handleFormSubmit = async (e) => {
        e.preventDefault();
        const { type, id, clientId } = state.editingContext;
        formModalSaveBtn.disabled = true;

        try {
            if (type === 'client') {
                const name = document.getElementById('clientName').value.trim();
                await api.addClient(name);
                showToast('Cliente agregado con éxito.');
            } else if (type === 'portal') {
                const portalData = {
                    nombre: document.getElementById('portalNombre').value.trim(),
                    url: document.getElementById('portalUrl').value.trim(),
                    usuario: document.getElementById('portalUsuario').value.trim(),
                    contra: document.getElementById('portalContra').value.trim(),
                };
                if (id) { // Editing
                    await api.updatePortal(id, portalData);
                    showToast('Portal actualizado con éxito.');
                } else { // Adding
                    await api.addPortal(clientId, portalData);
                    showToast('Portal agregado con éxito.');
                }
            }
            formModal.hide();
            await refreshData();
        } catch (error) {
            // Toast is shown by the api helper
        } finally {
            formModalSaveBtn.disabled = false;
        }
    };
    
    // --- EVENT HANDLERS ---
    const handleActionClick = async (e) => {
        const target = e.target.closest('[data-action]');
        if (!target) return;

        const { action, clientId, clientName, portalId, portalName, text } = target.dataset;

        switch (action) {
            case 'show-add-client-modal':
                openModalForClient();
                break;
            case 'delete-client':
                if (confirm(`¿Estás seguro de que quieres eliminar al cliente "${clientName}" y todos sus portales?`)) {
                    try {
                        await api.deleteClient(clientId);
                        showToast(`Cliente "${clientName}" eliminado.`);
                        await refreshData();
                    } catch (error) { /* Handled by api helper */ }
                }
                break;
            case 'show-add-portal-modal':
                openModalForPortal(null, clientId);
                break;
            case 'show-edit-portal-modal':
                openModalForPortal(portalId);
                break;
            case 'delete-portal':
                if (confirm(`¿Estás seguro de que quieres eliminar el portal "${portalName}"?`)) {
                    try {
                        await api.deletePortal(portalId);
                        showToast(`Portal "${portalName}" eliminado.`);
                        await refreshData();
                    } catch (error) { /* Handled by api helper */ }
                }
                break;
            case 'copy-text':
                navigator.clipboard.writeText(text).then(() => {
                    const originalIcon = target.innerHTML;
                    target.innerHTML = '<i class="bi bi-check-lg text-success"></i>';
                    setTimeout(() => { target.innerHTML = originalIcon; }, 1500);
                }).catch(err => showToast('No se pudo copiar.', true));
                break;
        }
    };
    
    // --- UTILITY FUNCTIONS ---
    const showToast = (message, isError = false) => {
        toastBody.textContent = message;
        toastTitle.textContent = isError ? 'Error' : 'Éxito';
        toastIcon.className = isError ? 'bi bi-exclamation-triangle-fill text-danger' : 'bi bi-check-circle-fill text-success';
        toast.show();
    };

    const findPortalById = (portalId) => {
        for (const client of state.clients) {
            const portal = client.portales.find(p => p.id === portalId);
            if (portal) return { foundClient: client, foundPortal: portal };
        }
        return { foundClient: null, foundPortal: null };
    };

    const refreshData = async () => {
        try {
            const portalData = await api.getPortals();
            state.clients = portalData;
            render();
        } catch (error) {
            clientCardsContainer.innerHTML = '<div class="alert alert-danger">No se pudieron cargar los datos. Intenta refrescar la página.</div>';
        }
    };

    // --- INITIALIZATION ---
    const init = async () => {
        render(); // Show initial loading spinner
        try {
            const me = await api.getMe();
            state.canManage = me.can_manage_portals;
            const portalData = await api.getPortals();
            state.clients = portalData;
            state.isLoading = false;
        } catch (error) {
            state.isLoading = false;
            clientCardsContainer.innerHTML = '<div class="alert alert-danger">No se pudieron cargar los datos. Intenta refrescar la página.</div>';
        }
        render(); // Render final state
    };

    // --- EVENT LISTENERS ---
    searchInput.addEventListener('input', (e) => {
        state.filterText = e.target.value;
        render();
    });
    document.body.addEventListener('click', handleActionClick);
    mainForm.addEventListener('submit', handleFormSubmit);

    // Start the application
    init();
});
