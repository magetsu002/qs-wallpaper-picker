import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import "."

FloatingWindow {
    id: root
    visible: true
    title: "wallpaper-picker"
    color: "transparent"

    property string onlineSearchScript: {
        let path = Qt.resolvedUrl("scripts/online_search.sh").toString()
        return path.startsWith("file://")
            ? decodeURIComponent(path.substring(7))
            : path
    }

    function invalidateOnlineSearch() {
        Quickshell.execDetached([
            "bash",
            root.onlineSearchScript,
            "--invalidate"
        ])
    }

    onVisibleChanged: {
        if (!visible) {
            Qt.quit()
        }
    }

    implicitWidth: Math.round(Screen.width * 0.94)
    implicitHeight: Math.round(Screen.height * 0.30)

    Shortcut {
        sequence: "Escape"
        context: Qt.ApplicationShortcut
        enabled: !picker.isApplying && picker.currentFilter !== "Search"
        onActivated: Qt.quit()
    }

    Shortcut {
        sequence: "Return"
        context: Qt.ApplicationShortcut
        enabled: picker.currentFilter === "Search"
                 && !picker.isApplying
                 && !picker.isSearchingOnline
        onActivated: {
            const normalized = String(picker.searchQuery || "").trim()
            if (normalized !== "") {
                picker.triggerOnlineSearch(normalized)
            }
        }
    }

    WallpaperPicker {
        id: picker
        anchors.fill: parent
        focus: true

        onSearchQueryChanged: root.invalidateOnlineSearch()
    }

    Rectangle {
        id: searchStatus
        z: 1000
        visible: picker.currentFilter === "Search"
        anchors.right: parent.right
        anchors.rightMargin: 18
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 14
        width: statusColumn.implicitWidth + 28
        height: statusColumn.implicitHeight + 18
        radius: 10
        color: "#B51B1B1B"
        border.width: 1
        border.color: "#55FFFFFF"

        Column {
            id: statusColumn
            anchors.centerIn: parent
            spacing: 3

            Text {
                text: {
                    if (picker.isSearchingOnline)
                        return "SEARCHING ONLINE"
                    if (picker.onlineSearchError !== "")
                        return "ONLINE SEARCH FAILED"
                    if (picker.isOnlineSearch)
                        return picker.visibleItemCount > 0
                            ? "ONLINE RESULTS"
                            : "NO ONLINE RESULTS"
                    return picker.visibleItemCount > 0
                        ? "LOCAL RESULTS"
                        : "NO LOCAL RESULTS"
                }
                color: "white"
                font.bold: true
                font.pixelSize: 12
                font.family: "JetBrains Mono"
            }

            Text {
                text: "Type to search locally • Press Enter to search online"
                color: "#D9FFFFFF"
                font.pixelSize: 11
                font.family: "JetBrains Mono"
            }
        }
    }
}
