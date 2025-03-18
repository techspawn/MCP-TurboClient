const ws = new WebSocket("ws://localhost:8080/chat");
    const chatBox = document.getElementById("chat-box");
    const userInput = document.getElementById("user-input");

    ws.onmessage = (event) => {
        addMessage(event.data, "assistant");
    };

    function sendMessage() {
        const message = userInput.value.trim();
        if (message) {
            addMessage(message, "user");
            ws.send(message);
            userInput.value = "";
        }
    }

    function addMessage(text, role) {
        const msgDiv = document.createElement("div");
        msgDiv.className = `message ${role}`;
        msgDiv.textContent = text;
        chatBox.appendChild(msgDiv);
        chatBox.scrollTop = chatBox.scrollHeight;

        anime({
            targets: msgDiv,
            opacity: [0, 1],
            translateY: [20, 0],
            duration: 500,
            easing: "easeOutExpo"
        });
    }

    function handleKeyPress(event) {
        if (event.key === "Enter") {
            sendMessage();
        }
    }