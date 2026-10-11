import QtQuick
import QtWebChannel
import QtWebEngine

/* Host del panel de Instant: ventana normal (con marco) con la UI en web.
   El QWebChannel se crea aca (la propiedad webChannel del view espera
   QQmlWebChannel, sin binding en PySide) y el bridge Python entra con
   registerBridge(). La pagina llega por `pageUrl` (se compone en gui.py
   inyectando qwebchannel.js). */
Window {
    id: host
    title: "Instant"
    width: 1100
    height: 600
    minimumWidth: 760
    minimumHeight: 480
    visible: false

    property url pageUrl

    // El cierre se maneja en QML (QQuickCloseEvent no cruza a PySide) y se
    // avisa con una señal simple: Python cierra la lógica y hace quit().
    signal closed()
    onClosing: function (close) {
        close.accepted = true
        host.closed()
    }

    WebChannel {
        id: channel
    }

    WebEngineView {
        id: view
        objectName: "view"
        anchors.fill: parent
        url: host.pageUrl
        webChannel: channel
        backgroundColor: "#0f1a20"
    }

    function registerBridge(obj) {
        channel.registerObject("bridge", obj)
    }
}
