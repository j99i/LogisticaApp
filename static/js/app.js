document.addEventListener('DOMContentLoaded', () => {

    // Lógica para el slideshow del cargador
    const slides = document.querySelectorAll('.loader-slide');
    if (slides.length > 0) {
        let currentSlideIndex = 0;
        let previousSlideIndex = -1;
        const showNextSlide = () => {
            if (previousSlideIndex !== -1) {
                slides[previousSlideIndex].classList.remove('active');
            }
            let newIndex;
            do {
                newIndex = Math.floor(Math.random() * slides.length);
            } while (newIndex === currentSlideIndex && slides.length > 1);
            currentSlideIndex = newIndex;
            slides[currentSlideIndex].classList.add('active');
            previousSlideIndex = currentSlideIndex;
        };
        showNextSlide(); 
        setInterval(showNextSlide, 5000);
    }
    
    let userPermissions = new Set();
    let currentUserRole = 'normal';

    // --- ELEMENTOS DEL DOM ---
    const searchInput = document.getElementById('searchInput');
    const clientFilter = document.getElementById('clientFilter');
    const channelFilter = document.getElementById('channelFilter');
    const loadingSpinner = document.getElementById('loading-spinner');
    const kpiContainer = document.getElementById('kpi-cards-container');
    const refreshBtn = document.getElementById('refresh-btn');
    const hoyOrdersContainer = document.getElementById('hoy-orders-container');
    const mananaOrdersContainer = document.getElementById('manana-orders-container');
    const groupBtn = document.getElementById('group-btn');
    const selectionCountSpan = document.getElementById('selection-count');

    // --- ESTADO DE LA APLICACIÓN ---
    let fullData = [];
    let currentOrder = null;
    let selectedOrders = new Set();
    const CACHE_KEY_PREFIX = 'logisticaDataCache_';

    // --- FUNCIONES AUXILIARES ---
    const parseDeliveryDateTime = (dateStr, timeStr) => {
        if (!dateStr || dateStr === 'Por Asignar') return null;
        const timeRegex = /^\d{1,2}:\d{2}(:\d{2})?$/;
        if (timeStr && timeRegex.test(timeStr.trim())) {
            try {
                const dt = new Date(`${dateStr}T${timeStr.trim()}`);
                if (!isNaN(dt.getTime())) return dt;
            } catch (e) { /* Ignorar */ }
        }
        try {
            const dt = new Date(`${dateStr}T00:00:00`);
            if (!isNaN(dt.getTime())) return dt;
        } catch (e) { /* Ignorar */ }
        return null;
    };

    const formatCurrency = (value) => `$${(value || 0).toLocaleString('es-MX', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

    const formatDisplayDate = (isoDate) => {
        if (!isoDate || isoDate === 'Por Asignar') {
            return 'Por Asignar';
        }
        try {
            const [year, month, day] = isoDate.split('-');
            return `${day}/${month}/${year}`;
        } catch (e) {
            return isoDate;
        }
    };

    // --- FUNCIONES DE RENDERIZADO ---
    const updateGroupButtonState = () => {
        if (!groupBtn || !selectionCountSpan) return;
        const count = selectedOrders.size;
        selectionCountSpan.textContent = count > 0 ? `(${count})` : '';
        if (count >= 2 && userPermissions.has('group_orders')) {
            groupBtn.disabled = false;
        } else {
            groupBtn.disabled = true;
        }
    };

    const calculatePriority = (deliveryDateStr) => {
        const deliveryDate = parseDeliveryDateTime(deliveryDateStr, '00:00:00');
        if (!deliveryDate) return 'Baja';
        const hoy = new Date();
        hoy.setHours(0, 0, 0, 0);
        const diffTime = deliveryDate - hoy;
        const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
        if (diffDays < 0) return 'Vencida';
        if (diffDays <= 2) return 'Urgente';
        if (diffDays <= 7) return 'Media';
        return 'Normal';
    };

    const renderPriority = (p) => {
        const map = { 'Vencida': { i: 'bi-exclamation-diamond-fill', c: 'text-danger' }, 'Urgente': { i: 'bi-exclamation-triangle-fill', c: 'text-warning' }, 'Media': { i: 'bi-clock-history', c: 'text-info' }, 'Normal': { i: 'bi-clock', c: 'text-success' }, 'Baja': { i: 'bi-calendar-check', c: 'text-secondary' } };
        const d = map[p] || map['Baja'];
        return `<i class="bi ${d.i} ${d.c}" title="${p}"></i>`;
    };

    const renderBadge = (e) => {
        const map = { 'Pendiente': 'bg-secondary', 'En Preparacion': 'bg-warning text-dark', 'En Ruta': 'bg-primary', 'Entregado': 'bg-success', 'Rechazo parcial': 'bg-danger', 'Rechazo total': 'bg-dark text-danger border border-danger' };
        return `<span class="badge ${map[e] || 'bg-light text-dark'}">${e}</span>`;
    };
    
    const renderTableRow = (row, isHistory = false) => {
        const tr = document.createElement('tr');
        if (isHistory) {
            const actionCell = userPermissions.has('archive_orders') ? `<td data-label="Acciones" class="text-center"><button class="btn btn-sm btn-outline-success release-btn" title="Liberar Orden" data-historial-id="${row.id}"><i class="bi bi-arrow-up-right-circle"></i></button></td>` : '<td data-label="Acciones"></td>';
            tr.innerHTML = `
                <td data-label="Orden de Compra">${row['Orden de compra']}</td>
                <td data-label="Cliente">${row.Cliente || 'N/A'}</td>
                <td data-label="Fecha Entrega">${formatDisplayDate(row['Fecha Entrega'])}</td>
                <td data-label="Estado Final">${renderBadge(row['Estado Final'])}</td>
                ${actionCell}`;
        } else {
            tr.className = ['Urgente', 'Vencida'].includes(row.Prioridad) ? 'table-danger-subtle' : '';
            const isSelected = selectedOrders.has(row['Orden de compra']);
            const checkboxCell = userPermissions.has('group_orders') ? `<td class="text-center"><input class="form-check-input order-checkbox" type="checkbox" data-oc="${row['Orden de compra']}" ${isSelected ? 'checked' : ''}></td>` : '<td></td>';
            const blockIcon = row.bloque_id ? `<i class="bi bi-collection-fill text-info me-2" title="Orden en bloque. ID: ${row.bloque_id}"></i>` : '';
            const actionCell = userPermissions.has('update_status') || userPermissions.has('edit_notes') || userPermissions.has('archive_orders') ? `<td data-label="Acciones" class="text-center"><button class="btn btn-sm btn-outline-primary action-btn" data-oc="${row['Orden de compra']}">Gestionar</button></td>` : '<td data-label="Acciones"></td>';
            tr.innerHTML = `
                ${checkboxCell}
                <td data-label="Prioridad" class="text-center">${renderPriority(row.Prioridad)}</td>
                <td data-label="Orden de Compra">${blockIcon}${row['Orden de compra']}</td>
                <td data-label="Cliente">${row.Cliente || 'N/A'}</td>
                <td data-label="Fecha Entrega">${formatDisplayDate(row['Fecha de entrega'])}</td>
                <td data-label="Horario">${row.Horario || 'N/D'}</td>
                <td data-label="Estado">${renderBadge(row.Estado)}</td>
                ${actionCell}`;
        }
        return tr;
    };

    const renderChecklist = (tareas) => {
        const container = document.getElementById('modal-checklist-container');
        const canEditTasks = userPermissions.has('update_status');
        if (!tareas || tareas.length === 0) {
            container.innerHTML = '<p class="text-muted small">No hay tareas definidas para esta orden.</p>';
            return;
        }
        container.innerHTML = tareas.map(t => `<div class="form-check"><input class="form-check-input" type="checkbox" value="" id="tarea-${t.id}" data-task-id="${t.id}" ${t.completado ? 'checked' : ''} ${canEditTasks ? '' : 'disabled'}><label class="form-check-label ${t.completado ? 'text-muted text-decoration-line-through' : ''}" for="tarea-${t.id}">${t.descripcion}</label></div>`).join('');
    };

    const renderDetails = (order) => {
        const container = document.getElementById('modal-details-container');
        const notesTextarea = document.getElementById('modal-notas');
        const saveNotesBtn = document.getElementById('save-notes-btn');
        if (userPermissions.has('edit_notes')) {
            notesTextarea.readOnly = false;
            saveNotesBtn.style.display = 'block';
        } else {
            notesTextarea.readOnly = true;
            saveNotesBtn.style.display = 'none';
        }
        if (!container) return;
        const subtotalFormateado = order.Subtotal ? formatCurrency(parseFloat(order.Subtotal)) : 'N/D';
        container.innerHTML = `<dt class="col-sm-4">Orden de Compra</dt><dd class="col-sm-8">${order['Orden de compra'] || 'N/D'}</dd><dt class="col-sm-4">SO</dt><dd class="col-sm-8">${order.SO || 'N/D'}</dd><dt class="col-sm-4">Factura</dt><dd class="col-sm-8">${order.Factura || 'N/D'}</dd><dt class="col-sm-4">Localidad Destino</dt><dd class="col-sm-8">${order['Localidad destino'] || 'N/D'}</dd><dt class="col-sm-4">No. Botellas</dt><dd class="col-sm-8">${order['No. Botellas'] || '0'}</dd><dt class="col-sm-4">No. Cajas</dt><dd class="col-sm-8">${order['No. Cajas'] || '0'}</dd><dt class="col-sm-4">Subtotal</dt><dd class="col-sm-8 fw-bold">${subtotalFormateado}</dd>`;
    };

    const renderStatusButtons = (estado) => {
        const container = document.getElementById('status-buttons-container');
        container.innerHTML = '';
        const finalStates = ['Entregado', 'Rechazo parcial', 'Rechazo total'];
        if (finalStates.includes(estado)) {
            if (userPermissions.has('archive_orders')) {
                container.innerHTML = `<button class="btn btn-info" id="archive-btn"><i class="bi bi-archive-fill"></i> Archivar y Mover a Historial</button>`;
            }
        } else {
            if (userPermissions.has('update_status')) {
                container.innerHTML = `<button class="btn btn-warning" data-estado="En Preparacion">En Preparación</button><button class="btn btn-primary" data-estado="En Ruta">En Ruta</button><button class="btn btn-success" data-estado="Entregado">Entregado</button><hr class="my-2"><button class="btn btn-danger" data-estado="Rechazo parcial">Rechazo Parcial</button><button class="btn btn-outline-danger" data-estado="Rechazo total">Rechazo Total</button>`;
            }
        }
    };

    const renderTable = (data, tableBodyId, isHistory = false) => {
        const tableBody = document.getElementById(tableBodyId);
        tableBody.innerHTML = '';
        const colspan = userPermissions.has('archive_orders') ? 5 : 4;
        if (data.length === 0) {
            const activeTableColspan = userPermissions.has('group_orders') ? 8 : 7;
            tableBody.innerHTML = `<tr><td colspan="${isHistory ? colspan : activeTableColspan}" class="text-center py-4">No se encontraron órdenes.</td></tr>`;
            return;
        }
        data.forEach(row => {
            const tr = renderTableRow(row, isHistory);
            tableBody.appendChild(tr);
        });
    };

    const processAndRenderSummaryTable = (orders, container, title, isTomorrow = false) => {
        if (!container) return;
        let content = `<h5><i class="bi ${isTomorrow ? 'bi-calendar-event' : 'bi-list-check'}"></i> ${title}</h5>`;
        if (orders.length === 0) {
            const message = isTomorrow ? "No hay entregas programadas para mañana." : "No hay entregas programadas para hoy.";
            content += `<div class="d-flex align-items-center justify-content-center h-100"><p class="text-center text-muted mt-4">${message}</p></div>`;
            container.innerHTML = content;
            return;
        }
        const displayItems = [];
        const processedBlockIds = new Set();
        orders.forEach(order => {
            if (order.bloque_id) {
                if (!processedBlockIds.has(order.bloque_id)) {
                    processedBlockIds.add(order.bloque_id);
                    const ordersInBlock = orders.filter(o => o.bloque_id === order.bloque_id);
                    displayItems.push({ type: 'block', id: order.bloque_id, Cliente: order.Cliente, Horario: order.Horario, Estado: order.Estado, orderCount: ordersInBlock.length, Fecha: order['Fecha de entrega'] });
                }
            } else {
                displayItems.push({ ...order, type: 'orden', Fecha: order['Fecha de entrega'] });
            }
        });
        const dateColumn = isTomorrow ? '<th>Fecha</th>' : '';
        content += `<div class="table-responsive mt-auto flex-grow-1 scrollable-card-list"><table class="table table-sm table-hover mb-0"><thead><tr><th>Cliente</th>${dateColumn}<th>Cita / Bloque</th><th>Horario</th><th>Estado</th></tr></thead><tbody>`;
        displayItems.forEach(item => {
            const badge = renderBadge(item.Estado);
            const dateCell = isTomorrow ? `<td>${formatDisplayDate(item.Fecha)}</td>` : '';
            if (item.type === 'block') {
                content += `<tr class="clickable-row" data-type="block" data-id="${item.id}"><td>${item.Cliente}</td>${dateCell}<td><i class="bi bi-collection-fill text-info"></i> Bloque (${item.orderCount} Órdenes)</td><td>${item.Horario || 'N/D'}</td><td>${badge}</td></tr>`;
            } else {
                content += `<tr class="clickable-row" data-type="orden" data-id="${item['Orden de compra']}"><td>${item.Cliente}</td>${dateCell}<td>${item['Orden de compra']}</td><td>${item.Horario || 'N/D'}</td><td>${badge}</td></tr>`;
            }
        });
        content += '</tbody></table></div>';
        container.innerHTML = content;
    };

    const renderHoyTable = (ordenesHoy) => {
        processAndRenderSummaryTable(ordenesHoy, hoyOrdersContainer, 'Órdenes para Hoy');
    };

    const renderMananaTable = (ordenesManana) => {
        processAndRenderSummaryTable(ordenesManana, mananaOrdersContainer, 'Órdenes para Mañana', true);
    };

    const renderNotesCard = (activeData) => {
        const container = document.getElementById('notes-list-container');
        if (!container) return;
        const ordersWithNotes = activeData.filter(order => order.Notas && order.Notas.trim() !== '');
        if (ordersWithNotes.length === 0) {
            container.innerHTML = '<div class="text-center text-muted p-4 d-flex align-items-center justify-content-center h-100">No hay órdenes con notas.</div>';
            return;
        }
        container.innerHTML = ordersWithNotes.map(order => {
            const noteSnippet = order.Notas.substring(0, 50) + (order.Notas.length > 50 ? '...' : '');
            const completeButton = userPermissions.has('edit_notes') ? `<button class="btn btn-sm btn-outline-success p-0 complete-note-btn" title="Marcar como lista" style="width: 28px; height: 28px;" data-oc="${order['Orden de compra']}"><i class="bi bi-check-lg"></i></button>` : '';
            return `<div class="list-group-item list-group-item-action note-item d-flex justify-content-between align-items-center"><a href="#" class="text-decoration-none text-white flex-grow-1 me-2" data-oc="${order['Orden de compra']}"><div class="d-flex w-100 justify-content-between"><h6 class="mb-1">OC: ${order['Orden de compra']}</h6><small class="text-muted">${order.Cliente}</small></div><p class="mb-1 text-white-50 small">${noteSnippet}</p></a>${completeButton}</div>`;
        }).join('');
    };

    const renderEnhancedKpis = (activeData) => {
        if (!kpiContainer) return;
        const ahora = new Date();
        const hoy = new Date(ahora);
        hoy.setHours(0, 0, 0, 0);
        const manana = new Date(ahora);
        manana.setDate(ahora.getDate() + 1);
        manana.setHours(0, 0, 0, 0);
        const pasadoManana = new Date(manana);
        pasadoManana.setDate(manana.getDate() + 1);
        const ordenesHoy = activeData.filter(d => {
            if (d.Estado === 'Entregado') return false;
            const fecha = parseDeliveryDateTime(d['Fecha de entrega'], d.Horario);
            return fecha && fecha >= hoy && fecha < manana;
        });
        const ordenesManana = activeData.filter(d => {
            if (d.Estado === 'Entregado') return false;
            const fecha = parseDeliveryDateTime(d['Fecha de entrega'], d.Horario);
            return fecha && fecha >= manana && fecha < pasadoManana;
        });
        const formatNumber = (value) => (value || 0).toLocaleString('es-MX');
        const totalActivas = activeData.length;
        const valorActivo = activeData.reduce((sum, d) => sum + (parseFloat(d.Subtotal) || 0), 0);
        const totalCitasHoy = ordenesHoy.length;
        const valorHoy = ordenesHoy.reduce((sum, d) => sum + (parseFloat(d.Subtotal) || 0), 0);
        const botellasHoy = ordenesHoy.reduce((sum, d) => sum + (parseInt(d['No. Botellas']) || 0), 0);
        const cajasHoy = ordenesHoy.reduce((sum, d) => sum + (parseInt(d['No. Cajas']) || 0), 0);
        const totalCitasManana = ordenesManana.length;
        const valorManana = ordenesManana.reduce((sum, d) => sum + (parseFloat(d.Subtotal) || 0), 0);
        kpiContainer.innerHTML = `<div class="col-lg-4 col-md-6 mb-4"><div class="card h-100 border-primary shadow-sm"><div class="card-body"><h5 class="card-title text-primary"><i class="bi bi-calendar-day"></i> Cargas de Hoy (${new Date().toLocaleDateString('es-ES', {day: 'numeric', month: 'long'})})</h5><p class="display-4 mb-1">${formatNumber(totalCitasHoy)} <span class="fs-4 text-muted">Citas</span></p><ul class="list-group list-group-flush"><li class="list-group-item d-flex justify-content-between"><strong>Valor Total:</strong> ${formatCurrency(valorHoy)}</li><li class="list-group-item d-flex justify-content-between"><strong>Volumen:</strong> ${formatNumber(cajasHoy)} Cajas / ${formatNumber(botellasHoy)} Botellas</li></ul></div></div></div><div class="col-lg-2 col-md-6 mb-4"><div class="card h-100 border-info"><div class="card-body text-center"><h5 class="card-title text-info"><i class="bi bi-calendar-event"></i> Para Mañana</h5><p class="display-5">${formatNumber(totalCitasManana)}</p><p class="text-muted mb-0">Citas</p><hr><p class="mb-0">${formatCurrency(valorManana)}</p></div></div></div><div class="col-lg-2 col-md-6 mb-4"><div class="card h-100 border-success"><div class="card-body text-center"><h5 class="card-title text-success"><i class="bi bi-clipboard-data"></i> Órdenes Activas</h5><p class="display-5">${formatNumber(totalActivas)}</p><p class="text-muted mb-0">Órdenes</p><hr><p class="mb-0">${formatCurrency(valorActivo)}</p></div></div></div><div class="col-lg-4 col-md-6 mb-4"><div class="card h-100"><div class="card-body d-flex flex-column"><h5 class="card-title mb-3"><i class="bi bi-journal-text"></i> Órdenes con Notas</h5><div id="notes-list-container" class="list-group list-group-flush flex-grow-1 scrollable-card-list"></div></div></div></div>`;
        renderHoyTable(ordenesHoy);
        renderMananaTable(ordenesManana);
        renderNotesCard(activeData);
    };

    const renderAllViews = (data) => {
        const activeData = data.filter(d => !d.Archivada);
        const urgentData = activeData.filter(d => ['Vencida', 'Urgente'].includes(d.Prioridad));
        const upcomingData = activeData.filter(d => d.Prioridad === 'Media');
        const normalData = activeData.filter(d => ['Normal', 'Baja'].includes(d.Prioridad));
        renderTable(urgentData, 'table-body-urgent');
        renderTable(upcomingData, 'table-body-upcoming');
        renderTable(normalData, 'table-body-normal');
        renderEnhancedKpis(activeData);
    };

    const applyFilters = () => {
        const searchTerm = searchInput.value.toLowerCase();
        const selectedClient = clientFilter.value;
        const filteredData = fullData.filter(item => {
            const matchesClient = !selectedClient || item.Cliente === selectedClient;
            const searchIn = `${item['Orden de compra'] || ''} ${item.SO || ''} ${item.Factura || ''}`.toLowerCase();
            const matchesSearch = !searchTerm || searchIn.includes(searchTerm);
            return matchesClient && matchesSearch;
        });
        if (searchTerm.trim() && filteredData.length > 0) {
            const firstResult = filteredData[0];
            let targetTabId = null;
            if (['Vencida', 'Urgente'].includes(firstResult.Prioridad)) {
                targetTabId = '#nav-urgent';
            } else if (firstResult.Prioridad === 'Media') {
                targetTabId = '#nav-upcoming';
            } else {
                targetTabId = '#nav-normal';
            }
            if (targetTabId) {
                const tabElement = document.querySelector(`button[data-bs-target="${targetTabId}"]`);
                if (tabElement) {
                    const tab = new bootstrap.Tab(tabElement);
                    tab.show();
                }
            }
        }
        selectedOrders.clear();
        updateGroupButtonState();
        renderAllViews(filteredData);
    };

    const populateChannelFilter = (channels) => {
        if (!channelFilter) return;
        channelFilter.innerHTML = '';
        if (currentUserRole === 'super') {
            channelFilter.innerHTML = '<option value="ALL">Todos los Canales</option>';
        }
        channels.forEach(channel => {
            const option = new Option(channel, channel);
            channelFilter.add(option);
        });
    };

    const saveData = async (url, body) => {
        loadingSpinner.classList.remove('d-none');
        try {
            const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.error || 'Error al guardar.');
            }
            return await response.json();
        } catch (error) {
            console.error(error);
            alert(`Falló la operación: ${error.message}`);
        } finally {
            loadingSpinner.classList.add('d-none');
        }
    };

    const initializeUI = (data) => {
        const uniqueClients = [...new Set(data.map(item => item.Cliente).filter(c => c))];
        clientFilter.innerHTML = '<option value="">Todos los Clientes</option>';
        uniqueClients.sort().forEach(c => clientFilter.add(new Option(c, c)));
        applyFilters();
    };

    const fetchData = async (forceRefresh = false, channel = null) => {
        const cacheKeyChannel = channel || 'initial';
        const CACHE_KEY = `${CACHE_KEY_PREFIX}${cacheKeyChannel}`;
        
        const processDataAndInitialize = (data) => {
            data.forEach(order => order.Prioridad = calculatePriority(order['Fecha de entrega']));
            fullData = data;
            sessionStorage.setItem(CACHE_KEY, JSON.stringify(fullData));
            initializeUI(fullData);
        };

        if (!forceRefresh) {
            const cachedData = sessionStorage.getItem(CACHE_KEY);
            if (cachedData) {
                console.log("Datos cargados desde la caché. ¡Navegación instantánea!");
                const data = JSON.parse(cachedData);
                processDataAndInitialize(data);
                document.getElementById('app-loader').classList.add('d-none');
                document.getElementById('app-container').classList.remove('d-none');
                return;
            }
        }

        loadingSpinner.classList.remove('d-none');
        try {
            const params = new URLSearchParams();
            if (channel) {
                params.append('canal', channel);
            }
            const response = await fetch(`/api/logistica/datos?${params.toString()}`);
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.error || 'Error del servidor');
            }
            const responseData = await response.json();
            populateChannelFilter(responseData.channels);
            channelFilter.value = responseData.loaded_channel;
            selectedOrders.clear();
            updateGroupButtonState();
            processDataAndInitialize(responseData.data);
            document.getElementById('app-loader').classList.add('d-none');
            document.getElementById('app-container').classList.remove('d-none');
        } catch (error) {
            console.error("Error al cargar los datos:", error);
            alert(`Error al cargar los datos: ${error.message}`);
            document.getElementById('app-loader').classList.add('d-none');
        } finally {
            loadingSpinner.classList.add('d-none');
        }
    };

    const initializeApp = async () => {
        try {
            const response = await fetch('/api/me');
            if (!response.ok) throw new Error('Usuario no autenticado');
            const user = await response.json();
            userPermissions = new Set(user.permissions);
            currentUserRole = user.rol;
            document.getElementById('user-info').textContent = `${user.nombre} (${user.rol})`;
            if (userPermissions.has('manage_users')) {
                const adminButtonContainer = document.getElementById('admin-button-container');
                const adminUrl = '/admin/users';
                adminButtonContainer.innerHTML = `<li><a class="dropdown-item" href="${adminUrl}"><i class="bi bi-people-fill me-2"></i>Admin Usuarios</a></li>`;
            }
            updateGroupButtonState();
            fetchData();
        } catch (error) {
            console.warn('Usuario no autenticado, mostrando pantalla de login.');
            document.getElementById('app-loader').classList.add('d-none');
            document.getElementById('login-container').classList.remove('d-none');
        }
    };

    const openDetailsModal = (oc) => {
        currentOrder = fullData.find(item => item['Orden de compra'] === oc);
        if (currentOrder) {
            const modalEl = document.getElementById('detailsModal');
            const modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
            document.getElementById('modal-oc-id').textContent = currentOrder['Orden de compra'];
            document.getElementById('modal-notas').value = currentOrder.Notas || '';
            renderChecklist(currentOrder.Tareas);
            renderDetails(currentOrder);
            renderStatusButtons(currentOrder.Estado);
            modalInstance.show();
        }
    };

    const openBlockDetailsModal = (blockId) => {
        const blockOrders = fullData.filter(o => o.bloque_id === blockId);
        if (blockOrders.length === 0) return;
        const totalBotellas = blockOrders.reduce((sum, order) => sum + (parseInt(order['No. Botellas']) || 0), 0);
        const totalCajas = blockOrders.reduce((sum, order) => sum + (parseInt(order['No. Cajas']) || 0), 0);
        const totalSubtotal = blockOrders.reduce((sum, order) => sum + (parseFloat(order.Subtotal) || 0), 0);
        const clientName = blockOrders[0]?.Cliente || 'Cliente Mixto';
        const modalHtmlContent = `<div class="container-fluid"><div class="row mb-3"><div class="col-4 text-center"><strong>Total Botellas</strong><p class="fs-4 mb-0">${totalBotellas.toLocaleString('es-MX')}</p></div><div class="col-4 text-center"><strong>Total Cajas</strong><p class="fs-4 mb-0">${totalCajas.toLocaleString('es-MX')}</p></div><div class="col-4 text-center"><strong>Subtotal Unificado</strong><p class="fs-4 mb-0">${formatCurrency(totalSubtotal)}</p></div></div><hr><h6>Órdenes Incluidas:</h6><div class="list-group" style="max-height: 250px; overflow-y: auto;">${blockOrders.map(order => `<a href="#" class="list-group-item list-group-item-action block-order-item" data-oc="${order['Orden de compra']}"><div class="d-flex w-100 justify-content-between"><h6 class="mb-1">OC: ${order['Orden de compra']}</h6>${renderBadge(order.Estado)}</div><p class="mb-1">${order.Cliente}</p></a>`).join('')}</div></div>`;
        Swal.fire({
            title: `<i class="bi bi-collection-fill"></i> ${clientName}`,
            html: modalHtmlContent,
            footer: `Bloque ID: ${blockId}`,
            width: '800px',
            showCloseButton: true,
            showConfirmButton: false,
            showDenyButton: userPermissions.has('group_orders'),
            denyButtonText: 'Desagrupar',
            denyButtonColor: '#5a6268',
            background: '#212529',
            color: '#ffffff',
            didOpen: () => {
                const swalContainer = Swal.getHtmlContainer();
                if (swalContainer) {
                    swalContainer.addEventListener('click', (e) => {
                        const orderItem = e.target.closest('.block-order-item');
                        if (orderItem) {
                            e.preventDefault();
                            const oc = orderItem.dataset.oc;
                            Swal.close();
                            openDetailsModal(oc);
                        }
                    });
                }
            }
        }).then(async (result) => {
            if (result.isDenied) {
                const ocsToUngroup = blockOrders.map(o => o['Orden de compra']);
                const response = await saveData('/api/desagrupar-bloque', { ocs: ocsToUngroup });
                if (response && response.success) {
                    Swal.fire('¡Éxito!', 'Las órdenes han sido desagrupadas.', 'success');
                    fetchData(true, channelFilter.value || 'ALL');
                }
            }
        });
    };

    const cargarHistorial = async () => {
        const cliente = document.getElementById('history-filter-cliente').value;
        const startDate = document.getElementById('history-filter-start-date').value;
        const endDate = document.getElementById('history-filter-end-date').value;
        const localidad = document.getElementById('history-filter-localidad').value;
        const canal = document.getElementById('history-filter-canal').value;
        const params = new URLSearchParams({ cliente, start_date: startDate, end_date: endDate, localidad, canal });
        const tableBody = document.getElementById('table-body-history');
        tableBody.innerHTML = `<tr><td colspan="5" class="text-center py-4">Cargando historial...</td></tr>`;
        try {
            const response = await fetch(`/api/historial?${params.toString()}`);
            if (!response.ok) throw new Error('No se pudo cargar el historial');
            const historyData = await response.json();
            tableBody.innerHTML = '';
            if (historyData.length === 0) {
                tableBody.innerHTML = `<tr><td colspan="5" class="text-center py-4">No se encontraron registros.</td></tr>`;
                return;
            }
            historyData.forEach(row => {
                const tr = renderTableRow(row, true);
                tableBody.appendChild(tr);
            });
        } catch (error) {
            console.error("Error al cargar el historial:", error);
            tableBody.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-danger">Error al cargar el historial.</td></tr>`;
        }
    };

    // --- EVENT LISTENERS ---
    if (searchInput) searchInput.addEventListener('input', applyFilters);
    if (clientFilter) clientFilter.addEventListener('change', applyFilters);
    if (channelFilter) channelFilter.addEventListener('change', () => {
        const selectedChannel = channelFilter.value;
        if (selectedChannel) {
            fetchData(false, selectedChannel);
        }
    });
    if (refreshBtn) refreshBtn.addEventListener('click', () => {
        const currentChannel = channelFilter.value || null;
        fetchData(true, currentChannel);
    });
    const navTabContent = document.getElementById('nav-tabContent');
    if (navTabContent) {
        navTabContent.addEventListener('click', e => {
            const actionBtn = e.target.closest('.action-btn');
            if (actionBtn) {
                const oc = actionBtn.dataset.oc;
                const order = fullData.find(o => o['Orden de compra'] === oc);
                if (!order) return;
                if (order.bloque_id) {
                    openBlockDetailsModal(order.bloque_id);
                } else {
                    openDetailsModal(oc);
                }
                return;
            }
            const releaseBtn = e.target.closest('.release-btn');
            if (releaseBtn) {
                const historialId = releaseBtn.dataset.historialId;
                if (confirm('¿Estás seguro de que quieres restaurar esta orden al seguimiento activo?')) {
                    fetch(`/api/orden/liberar/${historialId}`, { method: 'POST' }).then(response => response.json()).then(result => {
                        if (result.success) {
                            alert('¡Orden restaurada!');
                            releaseBtn.closest('tr').remove();
                            fetchData(true, channelFilter.value || 'ALL');
                        } else {
                            alert(`Error: ${result.error || 'No se pudo restaurar la orden.'}`);
                        }
                    }).catch(err => {
                        console.error('Error al restaurar:', err);
                        alert('Falló la conexión al intentar restaurar la orden.');
                    });
                }
            }
        });
        navTabContent.addEventListener('change', e => {
            if (e.target.classList.contains('order-checkbox')) {
                const oc = e.target.dataset.oc;
                if (e.target.checked) {
                    selectedOrders.add(oc);
                } else {
                    selectedOrders.delete(oc);
                }
                updateGroupButtonState();
            }
        });
    }
    if (kpiContainer) kpiContainer.addEventListener('click', async (e) => {
        const noteLink = e.target.closest('a.text-decoration-none');
        if (noteLink) {
            e.preventDefault();
            const orderId = noteLink.dataset.oc;
            if (orderId) {
                openDetailsModal(orderId);
            }
            return;
        }
        const completeBtn = e.target.closest('.complete-note-btn');
        if (completeBtn) {
            const orderId = completeBtn.dataset.oc;
            if (confirm(`¿Estás seguro de que quieres limpiar las notas de la orden ${orderId}? Esta acción no se puede deshacer.`)) {
                const result = await saveData('/api/orden/clear-notes', { orden_compra: orderId });
                if (result && result.success) {
                    const orderInCache = fullData.find(o => o['Orden de compra'] === orderId);
                    if (orderInCache) {
                        orderInCache.Notas = '';
                    }
                    sessionStorage.setItem(`${CACHE_KEY_PREFIX}${channelFilter.value || 'ALL'}`, JSON.stringify(fullData));
                    applyFilters();
                }
            }
        }
    });
    const saveNotesBtn = document.getElementById('save-notes-btn');
    if (saveNotesBtn) saveNotesBtn.addEventListener('click', async () => {
        if (currentOrder) {
            currentOrder.Notas = document.getElementById('modal-notas').value;
            await saveData('/api/actualizar-notas', { orden_compra: currentOrder['Orden de compra'], notas: currentOrder.Notas });
            const cachedOrder = fullData.find(o => o['Orden de compra'] === currentOrder['Orden de compra']);
            if (cachedOrder) cachedOrder.Notas = currentOrder.Notas;
            sessionStorage.setItem(`${CACHE_KEY_PREFIX}${channelFilter.value || 'ALL'}`, JSON.stringify(fullData));
            applyFilters();
            alert('Notas guardadas.');
        }
    });
    const checklistContainer = document.getElementById('modal-checklist-container');
    if(checklistContainer) checklistContainer.addEventListener('change', async (e) => {
        if (e.target.matches('.form-check-input')) {
            const tareaId = parseInt(e.target.dataset.taskId);
            const completado = e.target.checked;
            await saveData('/api/actualizar-tarea', { tarea_id: tareaId, completado: completado });
            const cachedOrder = fullData.find(o => o['Orden de compra'] === currentOrder['Orden de compra']);
            if (cachedOrder) {
                const tarea = cachedOrder.Tareas.find(t => t.id === tareaId);
                if (tarea) tarea.completado = completado;
            }
            sessionStorage.setItem(`${CACHE_KEY_PREFIX}${channelFilter.value || 'ALL'}`, JSON.stringify(fullData));
        }
    });
    const detailsModalEl = document.getElementById('detailsModal');
    if (detailsModalEl) {
        detailsModalEl.addEventListener('hidden.bs.modal', () => {
            document.body.focus();
        });
        detailsModalEl.addEventListener('click', async (e) => {
            if (e.target.matches('[data-estado]')) {
                const nuevoEstado = e.target.dataset.estado;
                const result = await saveData('/api/actualizar-estado', { orden_compra: currentOrder['Orden de compra'], nuevo_estado: nuevoEstado });
                if (result && result.success) {
                    result.updated_ocs.forEach(oc => {
                        const orderToUpdate = fullData.find(o => o['Orden de compra'] === oc);
                        if (orderToUpdate) {
                            orderToUpdate.Estado = nuevoEstado;
                        }
                    });
                    currentOrder.Estado = nuevoEstado;
                    sessionStorage.setItem(`${CACHE_KEY_PREFIX}${channelFilter.value || 'ALL'}`, JSON.stringify(fullData));
                    applyFilters();
                    renderStatusButtons(nuevoEstado);
                }
            }
            if (e.target.id === 'archive-btn') {
                const archiveOrder = async (orderToArchive) => {
                    if (orderToArchive.bloque_id) {
                        const blockOrders = fullData.filter(o => o.bloque_id === orderToArchive.bloque_id);
                        const result = await saveData('/api/archivar-bloque', { orders_data: blockOrders });
                        if (result && result.success) {
                            const ocsToArchive = new Set(blockOrders.map(o => o['Orden de compra']));
                            fullData = fullData.filter(order => !ocsToArchive.has(order['Orden de compra']));
                            return true;
                        }
                    } else {
                        const result = await saveData('/api/archivar-orden', { ...orderToArchive });
                        if (result && result.success) {
                            fullData = fullData.filter(order => order['Orden de compra'] !== orderToArchive['Orden de compra']);
                            return true;
                        }
                    }
                    return false;
                };
                const wasArchived = await archiveOrder(currentOrder);
                if (wasArchived) {
                    sessionStorage.setItem(`${CACHE_KEY_PREFIX}${channelFilter.value || 'ALL'}`, JSON.stringify(fullData));
                    applyFilters();
                    const modalInstance = bootstrap.Modal.getInstance(detailsModalEl);
                    if (modalInstance) modalInstance.hide();
                    alert('Operación de archivado completada.');
                }
            }
        });
    }
    const historyTabEl = document.querySelector('button[data-bs-target="#nav-history"]');
    if(historyTabEl) {
        historyTabEl.addEventListener('show.bs.tab', async () => {
            cargarHistorial();
            try {
                const response = await fetch('/api/channels');
                if (!response.ok) return;
                const channels = await response.json();
                const channelFilterEl = document.getElementById('history-filter-canal');
                channelFilterEl.innerHTML = '<option value="ALL">Todos los Canales</option>';
                channels.forEach(channel => {
                    channelFilterEl.add(new Option(channel, channel));
                });
            } catch (error) {
                console.error('Error al cargar canales para el filtro:', error);
            }
        });
    }
    const historyFilterBtn = document.getElementById('history-filter-btn');
    if(historyFilterBtn) historyFilterBtn.addEventListener('click', cargarHistorial);
    const historyDownloadBtn = document.getElementById('history-download-btn');
    if(historyDownloadBtn) {
        historyDownloadBtn.addEventListener('click', () => {
            const cliente = document.getElementById('history-filter-cliente').value;
            const startDate = document.getElementById('history-filter-start-date').value;
            const endDate = document.getElementById('history-filter-end-date').value;
            const localidad = document.getElementById('history-filter-localidad').value;
            const canal = document.getElementById('history-filter-canal').value;
            const params = new URLSearchParams({ cliente, start_date: startDate, end_date: endDate, localidad, canal });
            window.location.href = `/api/historial/descargar?${params.toString()}`;
        });
    }
    if (groupBtn) {
        groupBtn.addEventListener('click', async () => {
            const count = selectedOrders.size;
            if (count < 2) return;
            const ocsToGroup = Array.from(selectedOrders);
            const result = await saveData('/api/crear-bloque', { ordenes_compra: ocsToGroup });
            
            if (result && result.success) {
                Swal.fire({
                    icon: 'success',
                    title: '¡Éxito!',
                    text: result.mensaje || 'Órdenes agrupadas con éxito.',
                    timer: 2000,
                    showConfirmButton: false
                });

                result.ordenes_agrupadas.forEach(oc => {
                    const orderInFullData = fullData.find(order => order['Orden de compra'] === oc);
                    if (orderInFullData) {
                        orderInFullData.bloque_id = result.bloque_id;
                    }
                });

                selectedOrders.clear();
                updateGroupButtonState();
                applyFilters();
            }
        });
    }

    function handleSummaryTableClick(event) {
        const row = event.target.closest('tr.clickable-row');
        if (!row) return;
        const type = row.dataset.type;
        const id = row.dataset.id;
        if (type === 'orden') {
            openDetailsModal(id);
        } else if (type === 'block') {
            openBlockDetailsModal(parseInt(id));
        }
    }
    if (hoyOrdersContainer) hoyOrdersContainer.addEventListener('click', handleSummaryTableClick);
    if (mananaOrdersContainer) mananaOrdersContainer.addEventListener('click', handleSummaryTableClick);
    
    // --- INICIO DE LA APLICACIÓN ---
    initializeApp();
});