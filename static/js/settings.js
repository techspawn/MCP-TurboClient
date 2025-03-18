function addServer() {
    const licenseKey = document.getElementById("licenseKey").value;
    fetch("/save_config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            licenseKey
        })
    })
    .then(response => response.json())
    .catch(error => console.error("Error:", error));
    // Reload the page
    location.reload();
}

function toggleApiKeyVisibility() {
    const apiKeyInput = document.getElementById('apiKeyDisplay');
    const toggleIcon = document.getElementById('toggleIcon');

    if (apiKeyInput.type === 'password') {
        apiKeyInput.type = 'text';
        toggleIcon.classList.remove('bi-eye');
        toggleIcon.classList.add('bi-eye-slash');
    } else {
        apiKeyInput.type = 'password';
        toggleIcon.classList.remove('bi-eye-slash');
        toggleIcon.classList.add('bi-eye');
    }
}

function saveWebSocketSettings() {
    const wsEndpoint = document.getElementById('wsEndpoint').value;
    // Save to backend or localStorage
    localStorage.setItem('wsEndpoint', wsEndpoint);
    alert('WebSocket settings saved!');
}