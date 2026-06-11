let API_URL = "http://127.0.0.1:8000"; // Fallback

async function fetchConfig() {
    try {
        const response = await fetch('./api_config.json?t=' + Date.now());
        const data = await response.json();
        if (data.api_url) {
            API_URL = data.api_url;
        }
    } catch (e) {
        console.warn("Could not load api_config.json, using fallback URL.");
    }
}

async function checkStatus() {
    await fetchConfig();
    try {
        const response = await fetch(`${API_URL}/status`);
        if (response.ok) {
            document.getElementById("status-indicator").textContent = "Online";
            document.getElementById("status-indicator").className = "status-online";
            return true;
        }
    } catch (e) {
        document.getElementById("status-indicator").textContent = "Offline (Click to Wake)";
        document.getElementById("status-indicator").className = "status-offline";
        return false;
    }
}

function appendMessage(role, text) {
    const chatHistory = document.getElementById("chat-history");
    const msgDiv = document.createElement("div");
    msgDiv.className = `message msg-${role}`;
    msgDiv.textContent = text;
    chatHistory.appendChild(msgDiv);
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

async function sendMessage() {
    const input = document.getElementById("user-input");
    const text = input.value.trim();
    if (!text) return;

    appendMessage("user", text);
    input.value = "";

    try {
        const response = await fetch(`${API_URL}/chat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text })
        });
        
        if (response.ok) {
            const data = await response.json();
            appendMessage("ali", data.answer);
        } else {
            appendMessage("ali", "Error connecting to ALI Engine.");
        }
    } catch (e) {
        appendMessage("ali", "Connection failed. Please ensure the server is awake.");
    }
}

document.getElementById("send-btn").addEventListener("click", sendMessage);
document.getElementById("status-indicator").addEventListener("click", () => {
    // In a real implementation, this could trigger a webhook to wake the GitHub Action
    alert("Triggering GitHub Action to wake server (Not fully wired in mock).");
    checkStatus();
});

// Initial check
checkStatus();
setInterval(checkStatus, 30000); // Check every 30s
