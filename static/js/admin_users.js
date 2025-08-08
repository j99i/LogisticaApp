// static/js/admin_users.js
document.addEventListener('DOMContentLoaded', () => {
    // NOTA: Se ha corregido el ID del contenedor y simplificado la carga de datos.
    const container = document.getElementById('user-management-container'); // <-- CORREGIDO
    if (!container) {
        console.error("El contenedor 'user-management-container' no fue encontrado en el DOM.");
        return;
    }

    const loadPermissions = async () => {
        try {
            // <<< INICIO: CÓDIGO MODIFICADO >>>
            // Hacemos una SOLA petición para obtener toda la información
            const response = await fetch('/api/users');
            if (!response.ok) {
                throw new Error(`Error del servidor: ${response.statusText}`);
            }
            const data = await response.json();
            
            // Extraemos toda la data necesaria de la respuesta única
            const { users, all_permissions, all_channels } = data;
            // <<< FIN: CÓDIGO MODIFICADO >>>
            
            if (users.length === 0) {
                 container.innerHTML = '<p class="text-muted">No hay usuarios para administrar.</p>';
                 return;
            }

            // Ya no necesitamos combinar datos, ¡vienen listos del backend!
            container.innerHTML = users.map(user => `
                <div class="card mb-4">
                    <div class="card-header">
                        <h5 class="mb-0">${user.nombre} <small class="text-muted">(${user.email})</small></h5>
                    </div>
                    <div class="card-body">
                        <h6>Permisos de Rol</h6>
                        <div class="row mb-3">
                            ${all_permissions.map(perm => `
                                <div class="col-md-4 col-sm-6">
                                    <div class="form-check form-switch">
                                        <input class="form-check-input role-permission-checkbox" type="checkbox" 
                                               id="user-${user.id}-perm-${perm.name}" data-user-id="${user.id}"
                                               value="${perm.name}" ${user.permissions.includes(perm.name) ? 'checked' : ''}>
                                        <label class="form-check-label" for="user-${user.id}-perm-${perm.name}" title="${perm.description}">
                                            ${perm.description}
                                        </label>
                                    </div>
                                </div>
                            `).join('')}
                        </div>
                        <button class="btn btn-primary btn-sm save-role-permissions-btn" data-user-id="${user.id}">Guardar Permisos</button>
                        <hr class="my-4">
                        <h6>Canales Permitidos</h6>
                        ${all_channels.length > 0 ? `
                            <div class="row">
                                ${all_channels.map(channel => `
                                    <div class="col-md-4 col-sm-6">
                                        <div class="form-check">
                                            <input class="form-check-input channel-permission-checkbox" type="checkbox" value="${channel}" 
                                                id="user-${user.id}-channel-${channel.replace(/\s+/g, '')}" 
                                                data-user-id="${user.id}" 
                                                ${user.allowed_channels.includes(channel) ? 'checked' : ''}>
                                            <label class="form-check-label" for="user-${user.id}-channel-${channel.replace(/\s+/g, '')}">
                                                ${channel}
                                            </label>
                                        </div>
                                    </div>
                                `).join('')}
                            </div>
                            <button class="btn btn-primary btn-sm mt-3 save-user-channels-btn" data-user-id="${user.id}">Guardar Canales</button>
                        ` : '<p class="text-muted small">No hay canales para asignar. Los canales se sincronizan automáticamente cuando se cargan los datos en el dashboard principal.</p>'}
                    </div>
                </div>
            `).join('');

        } catch (error) {
            console.error('Error al cargar la configuración de usuarios:', error);
            container.innerHTML = '<p class="text-danger">No se pudo cargar la configuración de usuarios.</p>';
        }
    };

    container.addEventListener('click', async (e) => {
        const button = e.target;
        const userId = button.dataset.userId;

        // Lógica para guardar Permisos de Rol
        if (button.classList.contains('save-role-permissions-btn')) {
            const checkboxes = container.querySelectorAll(`.role-permission-checkbox[data-user-id="${userId}"]`);
            const selectedPermissions = Array.from(checkboxes)
                .filter(cb => cb.checked)
                .map(cb => cb.value);

            button.disabled = true;
            button.textContent = 'Guardando...';

            try {
                const response = await fetch(`/api/users/${userId}/permissions`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ permissions: selectedPermissions })
                });
                const result = await response.json();
                if (!result.success) throw new Error(result.error);
                alert(result.message);
            } catch (error) {
                console.error('Error al guardar permisos:', error);
                alert('Hubo un error al guardar los permisos de rol.');
            } finally {
                button.disabled = false;
                button.textContent = 'Guardar Permisos';
            }
        }

        // Lógica para guardar Permisos de Canal
        if (button.classList.contains('save-user-channels-btn')) {
            const checkboxes = container.querySelectorAll(`.channel-permission-checkbox[data-user-id="${userId}"]`);
            const selectedChannels = Array.from(checkboxes)
                .filter(cb => cb.checked)
                .map(cb => cb.value);

            button.disabled = true;
            button.textContent = 'Guardando...';

            try {
                const response = await fetch(`/api/users/${userId}/channels`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ channels: selectedChannels })
                });
                const result = await response.json();
                if (!result.success) throw new Error(result.error);
                alert(result.message);
            } catch (error) {
                console.error('Error al guardar canales:', error);
                alert('Hubo un error al guardar los permisos de canal.');
            } finally {
                button.disabled = false;
                button.textContent = `Guardar Canales`;
            }
        }
    });

    loadPermissions();
});