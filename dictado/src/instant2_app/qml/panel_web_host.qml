import QtQuick
import QtQuick.Dialogs
import QtWebChannel
import QtWebEngine

/* Host del panel de Instant2: ventana normal (con marco) con la UI en web.
   El QWebChannel se crea aca (la propiedad webChannel del view espera
   QQmlWebChannel, sin binding en PySide) y el bridge Python entra con
   registerBridge(). La pagina llega por `pageUrl` (se compone en gui.py
   inyectando qwebchannel.js). */
Window {
    id: host
    title: "Instant2"
    width: 1100
    height: 600
    minimumWidth: 760
    minimumHeight: 480
    visible: false

    property url pageUrl
    property var bridgeObject: null
    property string fileDialogKind: ""

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
        backgroundColor: "#10191d"
    }

    FileDialog {
        id: runtimeFileDialog
        title: host.fileDialogKind === "llm_model_path"
            ? "Elegir archivo de lenguaje" : "Elegir archivo del servicio"
        fileMode: FileDialog.OpenFile
        nameFilters: host.fileDialogKind === "llm_model_path"
            ? ["Archivos de lenguaje (*)", "Todos los archivos (*)"]
            : ["Archivos ejecutables (*.exe)", "Todos los archivos (*)"]
        onAccepted: {
            if (host.bridgeObject)
                host.bridgeObject.fileSelected(host.fileDialogKind, selectedFile.toLocalFile())
        }
    }

    function registerBridge(obj) {
        host.bridgeObject = obj
        channel.registerObject("bridge", obj)
    }

    function openFileDialog(kind) {
        host.fileDialogKind = kind
        runtimeFileDialog.open()
    }
}
