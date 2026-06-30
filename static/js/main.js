// Toast & UI Notifications
document.addEventListener('DOMContentLoaded', () => {
    // Automatically fade out flash messages after 4 seconds
    const flashMessages = document.querySelectorAll('.flash-message');
    flashMessages.forEach(msg => {
        setTimeout(() => {
            msg.style.opacity = '0';
            msg.style.transform = 'translateY(-20px)';
            msg.style.transition = 'all 0.5s ease';
            setTimeout(() => msg.remove(), 500);
        }, 4000);
    });
});

function showToast(message, category = 'error') {
    const container = document.querySelector('.flash-messages');
    if (!container) return;
    const msgEl = document.createElement('div');
    msgEl.className = `flash-message ${category}`;
    msgEl.innerHTML = `
        <span>
            ${category === 'success' 
                ? '<i class="fa-solid fa-circle-check" style="color: var(--accent-emerald); margin-right: 8px;"></i>' 
                : '<i class="fa-solid fa-circle-exclamation" style="color: var(--accent-rose); margin-right: 8px;"></i>'}
            ${message}
        </span>
        <button onclick="this.parentElement.remove()" style="background: none; border: none; color: var(--text-secondary); cursor: pointer; margin-left: 8px;"><i class="fa-solid fa-xmark"></i></button>
    `;
    container.appendChild(msgEl);
    
    setTimeout(() => {
        msgEl.style.opacity = '0';
        msgEl.style.transform = 'translateY(-20px)';
        msgEl.style.transition = 'all 0.5s ease';
        setTimeout(() => msgEl.remove(), 500);
    }, 4000);
}

// Modal Operations
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'flex';
    }
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'none';
    }
}

// Window click listener to close modals if clicking overlay
window.addEventListener('click', (event) => {
    if (event.target.classList.contains('modal-overlay')) {
        event.target.style.display = 'none';
    }
});
