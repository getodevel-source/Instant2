import QtQuick
import QtWebChannel
import QtWebEngine

/* Host del overlay web del daemon: la misma ventana (transparente, siempre
   encima, sin foco) que usaba Qt Quick, con la UI en HTML/JS corriendo en
   QtWebEngine. El QWebChannel se crea aca (la propiedad webChannel del view
   espera QQmlWebChannel, sin binding en PySide) y el bridge Python entra con
   registerBridge(). La pagina llega por `pageUrl` (se compone en
   overlay_web.py inyectando qwebchannel.js). */
Window {
    id: host
    title: "Instant2 overlay"
    width: 480
    height: 140
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
           | Qt.WindowDoesNotAcceptFocus
    visible: true

    property url pageUrl

    WebChannel {
        id: channel
    }

    WebEngineView {
        id: view
        objectName: "view"
        anchors.fill: parent
        url: host.pageUrl
        webChannel: channel
        backgroundColor: "transparent"
    }

    function registerBridge(obj) {
        channel.registerObject("bridge", obj)
    }
}
