import QtQuick
import QtQuick.Layouts
import QtQuick.Window
import QtCore
import Qt.labs.folderlistmodel
import QtMultimedia
import Quickshell
import Quickshell.Io
import "../" 
import QtQuick.Dialogs
import "config"

Item {
    Settings {
        id: settings
    }

    FileDialog {
        id: fileDialog
        title: "Select Wallpaper"
        nameFilters: ["Images (*.png *.jpg *.jpeg *.webp *.gif)"]
        fileMode: FileDialog.OpenFiles

        onAccepted: {
            for (let i = 0; i < selectedFiles.length; i++) {
                window.importWallpaper(selectedFiles[i])
            }
            window.reloadFolder()
        }
    }

    id: window
    width: Screen.width

    // --- Responsive Scaling Logic ---
    Scaler {
        id: scaler
        currentWidth: Screen.width
    }
    
    function s(val) { 
        return scaler.s(val); 
    }

    function anim(ms) {
        return settings.uiAnimationsEnabled ? Math.max(0, Math.round(ms * settings.uiAnimationScale)) : 0;
    }

    readonly property var safeTransitions: ["fade", "wipe", "wave", "grow", "center", "outer", "any"]

    function normalizeTransition(name) {
        const t = String(name || "").trim().toLowerCase();
        if (t === "" || t === "random") return "random";
        return window.safeTransitions.indexOf(t) !== -1 ? t : "fade";
    }

    function pickTransition() {
        const chosen = normalizeTransition(settings.wallpaperTransitionType);
        if (chosen !== "random") {
            return chosen;
        }
        return window.safeTransitions[Math.floor(Math.random() * window.safeTransitions.length)];
    }

    function requestClose() {
        closeAfterApply.restart();
    }

    Timer {
        id: closeAfterApply
        interval: settings.closeDelayMs
        repeat: false
        onTriggered: Qt.quit()
    }

    MatugenColors { id: _theme }

    // -------------------------------------------------------------------------
    // PROPERTIES & IPC RECEIVER
    // -------------------------------------------------------------------------
    property string widgetArg: ""
    property string targetWallName: ""
    property bool initialFocusSet: false
    property int visibleItemCount: -1
    property int scrollAccum: 0
    property real scrollThreshold: window.s(300) 

    // Filter System Properties
    property string currentFilter: "All"
    property string _lastFilter: "All"
    property string searchQuery: ""
    property bool isOnlineSearch: false
    property bool isSearchingOnline: false
    property string onlineSearchError: ""
    property bool isSearchPaused: false
    property bool hasSearched: false 
    property var colorMap: ({})
    property int cacheVersion: 0 
    
    // Download and Status Tracking Properties
    property bool isDownloadingWallpaper: false
    property string currentDownloadName: ""
    property string pendingOnlineDownloadName: ""
    property string pendingOnlineDownloadDestination: ""
    
    // STRICT ARCHITECTURAL LOCK
    property bool isApplying: false 
    
    // Reactive Status Properties
    property bool isStartup: localFolderModel.status === FolderListModel.Loading || srcModel.status === FolderListModel.Loading
    property bool isReady: visible && localFolderModel.status === FolderListModel.Ready
    property bool isSearchActive: false
    
    // Memory Properties for Search
    property string lastSearchName: ""
    property bool isModelChanging: false
    property bool searchIndexRestored: false
    
    // Lock scrolling/interaction while actively streaming search results.
    property bool isScrollingBlocked: false
    property bool jumpToLastOnFilterChange: false

    readonly property var filterData: [
        { name: "All", hex: "", label: "All" },
        { name: "Video", hex: "", label: "Vid" },
        { name: "Red", hex: "#FF4500", label: "" },
        { name: "Orange", hex: "#FFA500", label: "" },
        { name: "Yellow", hex: "#FFD700", label: "" },
        { name: "Green", hex: "#32CD32", label: "" },
        { name: "Blue", hex: "#1E90FF", label: "" },
        { name: "Purple", hex: "#8A2BE2", label: "" },
        { name: "Pink", hex: "#FF69B4", label: "" },
        { name: "Monochrome", hex: "#A9A9A9", label: "" },
        { name: "Search", hex: "", label: "Search" } 
    ]

    function resolveProjectScript(relativePath) {
        let scriptPath = Qt.resolvedUrl(relativePath).toString()
        if (scriptPath.startsWith("file://")) {
            scriptPath = decodeURIComponent(scriptPath.substring(7))
        }
        return scriptPath
    }

    function startOnlineDownload(safeFileName, destination) {
        if (!safeFileName || !destination ||
            window.isDownloadingWallpaper || onlineDownloadProcess.running) {
            return
        }

        window.isDownloadingWallpaper = true
        window.isApplying = true
        window.currentDownloadName = safeFileName
        window.pendingOnlineDownloadName = safeFileName
        window.pendingOnlineDownloadDestination = destination
        window.onlineSearchError = ""

        onlineDownloadProcess.command = [
            "bash",
            window.resolveProjectScript("scripts/online_search.sh"),
            "--download",
            safeFileName,
            "--destination",
            destination
        ]
        onlineDownloadProcess.running = true
    }

    function applyDownloadedOnlineWallpaper(safeFileName) {
        if (!safeFileName) {
            window.isApplying = false
            return
        }

        window.isApplying = true
        window.targetWallName = safeFileName

        const escapeBash = (str) => String(str).replace(/(["\\$`])/g, '\\$1')
        const boolEnv = (value) => value ? "1" : "0"
        const destFile = window.srcDir + "/" + safeFileName
        const finalThumb = decodeURIComponent(window.thumbDir.replace("file://", "")) + "/" + safeFileName
        const tempThumb = decodeURIComponent(window.searchDir.replace("file://", "")) + "/" + safeFileName
        const reloadScript = window.resolveProjectScript("scripts/matugen_reload.sh")
        const randomTransition = window.transitions[Math.floor(Math.random() * window.transitions.length)]

        const applyScript = `
            (
                export DEST_FILE="${escapeBash(destFile)}"
                export FINAL_THUMB="${escapeBash(finalThumb)}"
                export TEMP_THUMB="${escapeBash(tempThumb)}"
                export RELOAD_SCRIPT="${escapeBash(reloadScript)}"
                export RANDOM_TRANSITION="${escapeBash(window.normalizeTransition(randomTransition))}"
                export TRANSITION_DURATION="${Number(settings.wallpaperTransitionDuration).toFixed(2)}"
                export TRANSITION_FPS="${Math.max(1, Number(settings.wallpaperTransitionFps))}"

                if [ -f "$TEMP_THUMB" ]; then
                    cp "$TEMP_THUMB" "$FINAL_THUMB" 2>/dev/null || true
                fi
                if command -v magick >/dev/null 2>&1; then
                    magick "$DEST_FILE" -resize x420 -quality 70 "$FINAL_THUMB" 2>/dev/null || true
                fi

                cp "$DEST_FILE" /tmp/lock_bg.png || true
                pkill mpvpaper || true

                (
                    ENABLE_DYNAMIC_COLORS="${boolEnv(settings.enableDynamicColors)}" \
                    ENABLE_MATUGEN="${boolEnv(settings.enableMatugen)}" \
                    ENABLE_HYPR_RELOAD="${boolEnv(settings.enableHyprReload)}" \
                    ENABLE_WAYBAR_RELOAD="${boolEnv(settings.enableWaybarReload)}" \
                    ENABLE_KITTY_RELOAD="${boolEnv(settings.enableKittyReload)}" \
                    ENABLE_CAVA_RELOAD="${boolEnv(settings.enableCavaReload)}" \
                    ENABLE_SWAYNC_RELOAD="${boolEnv(settings.enableSwayncReload)}" \
                    ENABLE_SWAYOSD_RELOAD="${boolEnv(settings.enableSwayosdReload)}" \
                    HYPR_COLORS_PATH="${escapeBash(settings.hyprColorsPath)}" \
                    WAYBAR_COLORS_PATH="${escapeBash(settings.waybarColorsPath)}" \
                    WAYBAR_LAUNCH_PATH="${escapeBash(settings.waybarLaunchPath)}" \
                    KITTY_SIGNAL_PROCESS="${escapeBash(settings.kittySignalProcess)}" \
                    EXTRA_RELOAD_COMMAND="${escapeBash(settings.extraReloadCommand)}" \
                    bash "$RELOAD_SCRIPT" "$DEST_FILE" || true
                ) &
                MATUGEN_PID=$!

                for i in {1..20}; do
                    if awww img --transition-type "$RANDOM_TRANSITION" --transition-duration "$TRANSITION_DURATION" --transition-fps "$TRANSITION_FPS" "$DEST_FILE" >/dev/null 2>&1; then
                        break
                    fi
                    if awww img --transition-type fade --transition-duration "$TRANSITION_DURATION" --transition-fps "$TRANSITION_FPS" "$DEST_FILE" >/dev/null 2>&1; then
                        break
                    fi
                    sleep 0.05
                done

                wait $MATUGEN_PID
            ) > /tmp/qs_apply.log 2>&1 & disown
        `

        Quickshell.execDetached(["bash", "-c", applyScript])
        window.requestClose()
    }

    // -------------------------------------------------------------------------
    // GLOBAL ACTION: APPLY WALLPAPER
    // -------------------------------------------------------------------------
    function applyWallpaper(safeFileName, isVideo) {
        if (!safeFileName || window.isApplying ||
            window.isDownloadingWallpaper || onlineDownloadProcess.running) {
            return
        }

        window.targetWallName = safeFileName

        if (window.currentFilter === "Search" && window.hasSearched) {
            const destFile = window.srcDir + "/" + safeFileName

            if (window.isDownloaded(safeFileName)) {
                window.applyDownloadedOnlineWallpaper(safeFileName)
            } else {
                window.startOnlineDownload(safeFileName, destFile)
            }
            return
        }

        window.isApplying = true

        let reloadScript = Qt.resolvedUrl("scripts/matugen_reload.sh").toString()
        
        if (reloadScript.startsWith("file://")) {
            reloadScript = decodeURIComponent(reloadScript.substring(7))
        }

        const boolEnv = (v) => v ? "1" : "0";
        const escapeBash = (str) => String(str).replace(/(["\\$`])/g, '\\$1');

        const realFileName =
            window.getSourceFileName(
                safeFileName,
                isVideo
            )
        const originalFile =
            window.srcDir + "/" + realFileName
        const thumbFile = Quickshell.env("HOME") + "/.cache/wallpaper_picker/thumbs/" + safeFileName 
        
        let wallpaperCmd = ""
        let lockBgCmd = ""
        
        const escOriginal = escapeBash(originalFile);
        const escThumb = escapeBash(thumbFile);
        const escReload = escapeBash(reloadScript);
        const lastType = isVideo ? "video" : "image";
        const ml4wMode = escapeBash(
            Quickshell.env("QS_WALLPAPER_ENABLE_ML4W")
            || "auto"
        );

        if (isVideo) {
            wallpaperCmd = `
                pkill mpvpaper || true
                awww clear || true
                swww clear || true
                mpvpaper -o 'loop --no-audio --hwdec=auto --profile=high-quality --video-sync=display-resample --interpolation --tscale=oversample --panscan=1.0 --video-unscaled=no' '*' "$WALL_FILE" >/tmp/mpvpaper.log 2>&1 &
            `
            lockBgCmd = `cp "$THUMB_FILE" /tmp/lock_bg.png`
        } else {
            wallpaperCmd = `
                pkill mpvpaper || true
                TRANSITION="${window.pickTransition()}"
                DURATION="${Number(settings.wallpaperTransitionDuration).toFixed(2)}"
                FPS="${Math.max(1, Number(settings.wallpaperTransitionFps))}"
                if ! awww img --transition-type "$TRANSITION" --transition-duration "$DURATION" --transition-fps "$FPS" "$WALL_FILE" >/dev/null 2>&1; then
                    awww img --transition-type fade --transition-duration "$DURATION" --transition-fps "$FPS" "$WALL_FILE"
                fi
            `
            lockBgCmd = `cp "$WALL_FILE" /tmp/lock_bg.png`
        }

        const fullScript = `
            (
                export WALL_FILE="${escOriginal}"
                export THUMB_FILE="${escThumb}"
                export RELOAD_SCRIPT="${escReload}"

                mkdir -p "$HOME/.cache/wallpaper_picker"
                printf '%s|%s\n' "${lastType}" "$WALL_FILE" > "$HOME/.cache/wallpaper_picker/last_wallpaper"

                # Optional ML4W/Rofi synchronization.
                (
                    ML4W_MODE="${ml4wMode}"
                    ML4W="$HOME/.cache/ml4w/hyprland-dotfiles"

                    if [ "$ML4W_MODE" = "1" ] ||
                       { [ "$ML4W_MODE" = "auto" ] && [ -d "$ML4W" ]; }
                    then
                        ML4W_SOURCE="$WALL_FILE"

                        if [ "${isVideo}" = "true" ]; then
                            ML4W_SOURCE="$THUMB_FILE"
                        fi

                        mkdir -p "$ML4W"
                        printf '%s\n' "$WALL_FILE" > "$ML4W/current_wallpaper"

                        if command -v magick >/dev/null 2>&1; then
                            magick "$ML4W_SOURCE" -resize 2048x1280^ -gravity center -extent 2048x1280 -blur 0x18 "$ML4W/blurred_wallpaper.png" 2>/dev/null || true
                        fi

                        printf '%s\n' '* { current-image: url("'"$ML4W"'/blurred_wallpaper.png", height); }' > "$ML4W/current_wallpaper.rasi"
                    fi
                ) >/tmp/ml4w_rofi_wallpaper_sync.log 2>&1 &
                echo "WALL_FILE=$WALL_FILE" > /tmp/qs_apply.log
                ${lockBgCmd} || true

                if [ "${isVideo}" = "true" ]; then
                    pkill mpvpaper || true
                    ${wallpaperCmd}
                else
                    ${wallpaperCmd}
                fi

                sleep 1
                ENABLE_DYNAMIC_COLORS="${boolEnv(settings.enableDynamicColors)}" ENABLE_MATUGEN="${boolEnv(settings.enableMatugen)}" ENABLE_HYPR_RELOAD="${boolEnv(settings.enableHyprReload)}" ENABLE_WAYBAR_RELOAD="${boolEnv(settings.enableWaybarReload)}" ENABLE_KITTY_RELOAD="${boolEnv(settings.enableKittyReload)}" ENABLE_CAVA_RELOAD="${boolEnv(settings.enableCavaReload)}" ENABLE_SWAYNC_RELOAD="${boolEnv(settings.enableSwayncReload)}" ENABLE_SWAYOSD_RELOAD="${boolEnv(settings.enableSwayosdReload)}" HYPR_COLORS_PATH="${escapeBash(settings.hyprColorsPath)}" WAYBAR_COLORS_PATH="${escapeBash(settings.waybarColorsPath)}" WAYBAR_LAUNCH_PATH="${escapeBash(settings.waybarLaunchPath)}" KITTY_SIGNAL_PROCESS="${escapeBash(settings.kittySignalProcess)}" EXTRA_RELOAD_COMMAND="${escapeBash(settings.extraReloadCommand)}" bash "$RELOAD_SCRIPT" "$( [ "${isVideo}" = "true" ] && echo "$THUMB_FILE" || echo "$WALL_FILE" )" || true
            ) >> /tmp/qs_apply.log 2>&1 & disown
        `
        Quickshell.execDetached(["bash", "-c", fullScript])
        window.requestClose()
    }

    // -------------------------------------------------------------------------
    // PERSISTENT SETTINGS
    // -------------------------------------------------------------------------
    QtObject {
        id: searchState
        property string query: ""
        property bool searched: false
        property string lastName: ""
    }

    // -------------------------------------------------------------------------
    // VISIBILITY LOGIC
    // -------------------------------------------------------------------------
   onVisibleChanged: {
       if (!visible) {
           window.initialFocusSet = false;
           window.searchIndexRestored = false;
           window.isApplying = false;
           if (window.hasSearched) {
               window.isSearchPaused = true;
           }
           Qt.quit();
       } else {
           if (window.hasSearched) {
               window.trySearchFocus();
           }
       }
   }
    // -------------------------------------------------------------------------
    property bool isLoading: localFolderModel.status === FolderListModel.Loading || 
                             srcModel.status === FolderListModel.Loading

    property bool showSpinner: window.isDownloadingWallpaper ||
                               window.isSearchingOnline ||
                               (window.currentFilter !== "Search" && window.isLoading)

    property string currentNotification: {
        if (window.isDownloadingWallpaper)
            return "Downloading wallpaper...";

        if (window.currentFilter === "Search") {
            if (window.isSearchingOnline)
                return "Searching Wallhaven...";

            if (window.onlineSearchError !== "")
                return window.onlineSearchError;

            if (!window.hasSearched)
                return "Type to search local wallpapers...";

            if (window.visibleItemCount === 0 && !window.isOnlineSearch)
                return "No local matches";

            if (window.visibleItemCount === 0)
                return "No online matches";

            return window.isOnlineSearch
                ? "Online results"
                : "Local search";
        }

        if (isLoading) return "Generating thumbnails...";
        if (window.visibleItemCount === 0) return "No wallpapers found";

        if (window.currentFilter === "All") return "";
        if (window.currentFilter === "Video") return "Videos";

        return window.currentFilter;
    }

    // Block the notification flag during initial load to stop UI shifting
    property bool showNotification: !window.isStartup && currentNotification !== ""

    function getCleanName(name) {
        if (!name) return "";
        let clean = String(name);
        return clean.startsWith("000_") ? clean.substring(4) : clean;
    }

    function getSourceFileName(name, isVideo) {
        let clean = String(name || "");

        if (!isVideo) {
            return clean.replace(/^thumb_/, "");
        }

        let encoded = clean
            .replace(/^000_/, "")
            .replace(/\.jpg$/i, "");

        for (let i = 0; i < srcModel.count; i++) {
            let candidate =
                srcModel.get(i, "fileName") || "";

            if (candidate === encoded) {
                return candidate;
            }

            let stem =
                candidate.replace(/\.[^.]+$/, "");

            if (stem === encoded) {
                return candidate;
            }
        }

        return encoded;
    }

    function importWallpaper(fileUrl) {
        let src = decodeURIComponent(fileUrl.toString().replace("file://", ""));
        let destDir = window.srcDir;

        let cmd = `cp "${src}" "${destDir}/"`;
        Quickshell.execDetached(["bash", "-c", cmd]);
    }

    function reloadFolder() {
        localFolderModel.folder = "";
        localFolderModel.folder = window.thumbDir;
        srcModel.folder = "";
        srcModel.folder = "file://" + window.srcDir;
    }

    function isDownloaded(name) {
        if (!name) return false;
        for (let i = 0; i < srcModel.count; i++) {
            if (srcModel.get(i, "fileName") === name) return true;
        }
        return false;
    }

    onWidgetArgChanged: {
        if (widgetArg !== "") {
            targetWallName = widgetArg;
            initialFocusSet = false; 
            tryFocus();
        }
    }

    function executeFocusRestore(targetIndex, isSearchRestore, requirePositioning) {
        let targetModel = window.getModelForFilter(window.currentFilter);
        
        if (targetIndex !== -1 && targetIndex < targetModel.count) {
            window.isModelChanging = true;
            
            if (requirePositioning) {
                view.forceLayout();
                view.positionViewAtIndex(targetIndex, ListView.Center);
            }
            
            view.currentIndex = targetIndex;
            
            if (isSearchRestore) {
                window.searchIndexRestored = true;
            }
            
            window.isModelChanging = false;
            window.initialFocusSet = true;
        } else if (isSearchRestore) {
            window.searchIndexRestored = true;
        }
    }

    function tryFocus() {
        if (initialFocusSet) return;

        if (localProxyModel.count > 0) {
            let foundIndex = -1;
            let cleanTarget = window.getCleanName(targetWallName);

            if (cleanTarget !== "") {
                for (let i = 0; i < localProxyModel.count; i++) {
                    let fname = localProxyModel.get(i).fileName || "";
                    if (window.getCleanName(fname) === cleanTarget) {
                        foundIndex = i;
                        break;
                    }
                }
            }

            let finalIndex = foundIndex !== -1 ? foundIndex : 0;
            window.executeFocusRestore(finalIndex, false, true);
        }
    }
    
    function trySearchFocus() {
        if (window.searchIndexRestored || localProxyModel.count === 0) return;

        if (window.lastSearchName === "") {
             window.searchIndexRestored = true;
             return;
        }

        for (let i = 0; i < localProxyModel.count; i++) {
            let fname = localProxyModel.get(i).fileName || "";
            if (fname === window.lastSearchName) {
                window.executeFocusRestore(i, true, true);
                return;
            }
        }

        window.searchIndexRestored = true;
    }

    function getModelForFilter(filter) {
        if (filter === "Search") return localProxyModel;
        return localProxyModel;
    }

    function updateVisibleCount() {
        let targetModel = window.getModelForFilter(window.currentFilter);
        
        if (!targetModel || targetModel.count === 0) {
            window.visibleItemCount = 0;
            return;
        }
        let count = 0;
        for (let i = 0; i < targetModel.count; i++) {
            let fname = targetModel.get(i).fileName || "";
            let isVid = fname.startsWith("000_");
            if (checkItemMatchesFilter(fname, isVid, window.cacheVersion, window.currentFilter)) {
                count++;
            }
        }
        window.visibleItemCount = count;
    }

    function triggerLocalSearch() {
        if (searchInput.text.trim() === "") return;

        window.isModelChanging = true;

        window.lastSearchName = "";
        searchState.lastName = "";

        window.currentFilter = "Search";
        window.searchIndexRestored = true;
        window.isOnlineSearch = false;
        window.hasSearched = true;
        window.isSearchPaused = true;
        window.searchQuery = searchInput.text.trim().toLowerCase();
        searchState.searched = true;
        searchState.query = window.searchQuery;

        view.currentIndex = 0;
        view.positionViewAtIndex(0, ListView.Center);

        window.isModelChanging = false;

        window.updateVisibleCount();
        window.applyFilters(true);

        if (window.visibleItemCount === 0) {
            window.triggerOnlineSearch(window.searchQuery);
        }

        searchInput.focus = false;
        view.forceActiveFocus();
    }

    readonly property string homeDir: "file://" + Quickshell.env("HOME")
    readonly property string thumbDir: "file://" + settings.thumbDir
    readonly property string searchDir: homeDir + "/.cache/wallpaper_picker/search_thumbs"
    readonly property string srcDir: settings.wallpaperDir

    readonly property var defaultTransitions: window.safeTransitions
    readonly property var transitions: settings.wallpaperTransitionType === "random" ? window.defaultTransitions : [window.normalizeTransition(settings.wallpaperTransitionType)]

    readonly property real itemWidth: window.s(400)
    readonly property real itemHeight: window.s(420)
    readonly property real borderWidth: window.s(3)
    readonly property real spacing: window.s(10)
    readonly property real skewFactor: -0.35

    Timer {
        id: scrollThrottle
        interval: settings.scrollThrottleMs 
    }

    property bool isFilterAnimating: false
    Timer {
        id: filterAnimationTimer
        interval: settings.filterAnimationMs
        onTriggered: window.isFilterAnimating = false
    }

    property bool isItemAnimating: false
    Timer {
        id: itemAnimationTimer
        interval: settings.itemAnimationMs
        onTriggered: window.isItemAnimating = false
    }

    // -------------------------------------------------------------------------
    // COLOR FILTERING MATH & NATIVE FILE SYSTEM CACHE
    // -------------------------------------------------------------------------
    function getHexBucket(hexStr) {
        if (!hexStr) return "Monochrome";
        
        hexStr = String(hexStr).trim().replace(/#/g, '');
        if (hexStr.length > 6) hexStr = hexStr.substring(0, 6);
        if (hexStr.length !== 6) return "Monochrome";

        let r = parseInt(hexStr.substring(0,2), 16) / 255;
        let g = parseInt(hexStr.substring(2,4), 16) / 255;
        let b = parseInt(hexStr.substring(4,6), 16) / 255;

        if (isNaN(r) || isNaN(g) || isNaN(b)) return "Monochrome";

        let max = Math.max(r, g, b), min = Math.min(r, g, b);
        let d = max - min;
        
        let h = 0;
        let s = max === 0 ? 0 : d / max;
        let v = max;

        if (max !== min) {
            if (max === r) {
                h = (g - b) / d + (g < b ? 6 : 0);
            } else if (max === g) {
                h = (b - r) / d + 2;
            } else {
                h = (r - g) / d + 4;
            }
            h /= 6;
        }
        h = h * 360; 

        if (s < 0.05 || v < 0.08) return "Monochrome";

        if (h >= 345 || h < 15) return "Red";
        if (h >= 15 && h < 45) return "Orange";
        if (h >= 45 && h < 75) return "Yellow";
        if (h >= 75 && h < 165) return "Green";
        if (h >= 165 && h < 260) return "Blue";
        if (h >= 260 && h < 315) return "Purple";
        if (h >= 315 && h < 345) return "Pink";

        return "Monochrome";
    }

    function checkItemMatchesFilter(fileName, isVid, cv, filter) {
        if (filter === "Search" && window.isOnlineSearch) {
            return true;
        }

        const cleanName =
            window.getCleanName(fileName).toLowerCase();
        const query =
            (window.searchQuery || "").trim().toLowerCase();

        if (filter === "Search") {
            return query === "" || cleanName.includes(query);
        }

        if (filter === "All") {
            return true;
        }

        if (filter === "Video") {
            return isVid;
        }

        if (isVid) {
            return false;
        }

        const hexColor = window.colorMap[String(fileName)];

        if (!hexColor) {
            return filter === "Monochrome";
        }

        return window.getHexBucket(hexColor) === filter;
    }

    FolderListModel {
        id: markerModel
        folder: "file://" + Quickshell.env("HOME") + "/.cache/wallpaper_picker/colors_markers"
        showDirs: false
        nameFilters: ["*_HEX_*"]
        
        onCountChanged: window.processMarkers()
        onStatusChanged: {
            if (status === FolderListModel.Ready) window.processMarkers()
        }
    }

    FolderListModel {
        id: srcModel
        folder: "file://" + window.srcDir
        nameFilters: ["*.jpg", "*.jpeg", "*.png", "*.webp", "*.gif", "*.mp4", "*.mkv", "*.mov", "*.webm"]
        showDirs: false
    }

    Process {
        id: onlineDownloadProcess

        onExited: (exitCode, exitStatus) => {
            const completedName = window.pendingOnlineDownloadName

            window.isDownloadingWallpaper = false
            window.currentDownloadName = ""
            window.pendingOnlineDownloadName = ""
            window.pendingOnlineDownloadDestination = ""

            if (exitCode === 0 && completedName !== "") {
                window.onlineSearchError = ""
                window.reloadFolder()
                window.applyDownloadedOnlineWallpaper(completedName)
                return
            }

            window.isApplying = false
            window.onlineSearchError = "Download failed"
            view.forceActiveFocus()
        }
    }

    Process {
        id: onlineSearchProcess

        stdout: StdioCollector {
            onStreamFinished: {
                window.applyOnlineResults(this.text)
            }
        }

        onExited: (exitCode, exitStatus) => {
            if (exitCode !== 0) {
                window.isSearchingOnline = false
                window.isOnlineSearch = false
                window.onlineSearchError = "Online search failed"

                localProxyModel.clear()
                window.syncLocalModel()
                window.updateVisibleCount()
            }
        }
    }

    function triggerOnlineSearch(query) {
        const normalized = String(query || "").trim()

        if (normalized === "" || onlineSearchProcess.running) {
            return
        }

        window.isOnlineSearch = true
        window.isSearchingOnline = true
        window.onlineSearchError = ""

        let scriptPath =
            Qt.resolvedUrl("scripts/online_search.sh").toString()

        if (scriptPath.startsWith("file://")) {
            scriptPath =
                decodeURIComponent(scriptPath.substring(7))
        }

        onlineSearchProcess.command = [
            "bash",
            scriptPath,
            normalized
        ]

        onlineSearchProcess.running = true
    }

    function applyOnlineResults(text) {
        window.isModelChanging = true
        localProxyModel.clear()

        const resultLines = String(text || "")
            .split("\n")
            .filter(line => line.trim() !== "")

        for (let i = 0; i < resultLines.length; i++) {
            const separator = resultLines[i].indexOf("|")

            if (separator <= 0) {
                continue
            }

            const fileName =
                resultLines[i].substring(0, separator).trim()

            if (fileName === "") {
                continue
            }

            const thumbnail =
                decodeURIComponent(
                    window.searchDir.replace("file://", "")
                )
                + "/"
                + fileName

            localProxyModel.append({
                fileName: fileName,
                fileUrl: "file://" + thumbnail
            })
        }

        window.isSearchingOnline = false
        window.isOnlineSearch = true
        window.onlineSearchError = ""
        window.hasSearched = true
        window.searchIndexRestored = true

        if (localProxyModel.count > 0) {
            view.currentIndex = 0
            view.positionViewAtIndex(0, ListView.Center)
        } else {
            view.currentIndex = -1
        }

        window.updateVisibleCount()
        window.isModelChanging = false
        view.forceActiveFocus()
    }

    function processMarkers() {
        let newMap = {};
        for (let i = 0; i < markerModel.count; i++) {
            let markerName = markerModel.get(i, "fileName") || "";
            if (!markerName) continue;
            
            let splitIdx = markerName.lastIndexOf("_HEX_");
            if (splitIdx !== -1) {
                let fName = markerName.substring(0, splitIdx);
                let hexCode = markerName.substring(splitIdx + 5);
                newMap[fName] = "#" + hexCode;
            }
        }
        window.colorMap = newMap;
        window.cacheVersion++; 
        window.updateVisibleCount();
    }

    function triggerColorExtraction() {
        const extractScript = `
            COLOR_DIR="$HOME/.cache/wallpaper_picker/colors_markers"
            THUMBS="$HOME/.cache/wallpaper_picker/thumbs"
            CSV="$HOME/.cache/wallpaper_picker/colors.csv"
            
            mkdir -p "$COLOR_DIR"
            
            if [ -f "$CSV" ]; then
                while IFS=, read -r fname hexcode; do
                    cleanhex=$(echo "$hexcode" | tr -d '\r#' | cut -c 1-6)
                    if [ -n "$cleanhex" ] && [ -n "$fname" ]; then
                        touch "$COLOR_DIR/$fname""_HEX_$cleanhex" 2>/dev/null
                    fi
                done < "$CSV"
                mv "$CSV" "$CSV.bak" 2>/dev/null
            fi
            
            if command -v magick &> /dev/null; then CMD="magick"; else CMD="convert"; fi
            
            for file in "$THUMBS"/*; do
                if [ -f "$file" ]; then
                    filename=$(basename "$file")
                    found=0
                    for marker in "$COLOR_DIR/$filename"_HEX_*; do
                        if [ -e "$marker" ]; then found=1; break; fi
                    done
                    
                    if [ $found -eq 0 ]; then
                        hex=$($CMD "$file" -modulate 100,200 -resize "1x1^" -gravity center -extent 1x1 -depth 8 -format "%[hex:p{0,0}]" info:- 2>/dev/null | grep -oE '[0-9A-Fa-f]{6}' | head -n 1)
                        if [ -n "$hex" ]; then
                            touch "$COLOR_DIR/$filename""_HEX_$hex"
                        fi
                    fi
                fi
            done
        `;
        Quickshell.execDetached(["bash", "-c", extractScript]);
    }

    function stepToNextValidIndex(direction) {
        let targetModel = window.getModelForFilter(window.currentFilter);
        if (!targetModel || targetModel.count === 0) return;
        
        let start = view.currentIndex;
        let found = -1;

        if (direction === 1) {
            for (let i = start + 1; i < targetModel.count; i++) {
                let fname = targetModel.get(i).fileName || "";
                let isVid = fname.startsWith("000_");
                if (checkItemMatchesFilter(fname, isVid, window.cacheVersion, window.currentFilter)) {
                    found = i; break;
                }
            }
        } else {
            for (let i = start - 1; i >= 0; i--) {
                let fname = targetModel.get(i).fileName || "";
                let isVid = fname.startsWith("000_");
                if (checkItemMatchesFilter(fname, isVid, window.cacheVersion, window.currentFilter)) {
                    found = i; break;
                }
            }
        }

        if (found !== -1) {
            view.currentIndex = found;
            return;
        }

        let filterOrder = ["All", "Video", "Red", "Orange", "Yellow", "Green", "Blue", "Purple", "Pink", "Monochrome"];
        let currentFilterIdx = filterOrder.indexOf(window.currentFilter);

        if (currentFilterIdx === -1) {
            let current = start;
            for (let i = 0; i < targetModel.count; i++) {
                current = (current + direction + targetModel.count) % targetModel.count;
                let fname = targetModel.get(current).fileName || "";
                let isVid = fname.startsWith("000_");
                
                if (checkItemMatchesFilter(fname, isVid, window.cacheVersion, window.currentFilter)) {
                    view.currentIndex = current;
                    return;
                }
            }
            return;
        }

        let nextFilterIdx = currentFilterIdx + direction;

        if (nextFilterIdx >= 0 && nextFilterIdx < filterOrder.length) {
            window.jumpToLastOnFilterChange = (direction === -1);
            window.currentFilter = filterOrder[nextFilterIdx];
        }
    }

    function cycleFilter(direction) {
        let currentIdx = -1;
        for (let i = 0; i < window.filterData.length; i++) {
            if (window.filterData[i].name === window.currentFilter) {
                currentIdx = i;
                break;
            }
        }
        
        if (currentIdx !== -1) {
            let nextIdx = (currentIdx + direction + window.filterData.length) % window.filterData.length;
            window.currentFilter = window.filterData[nextIdx].name;
        }
    }

    function applyFilters(forceSnap) {
        let targetModel = window.getModelForFilter(window.currentFilter);
        
        if (!targetModel || targetModel.count === 0) {
            window.updateVisibleCount();
            return;
        }

        if (window.currentFilter === "Search") {
            window.updateVisibleCount();
            return; 
        }

        let firstValidIndex = -1;
        let lastValidIndex = -1;
        let cleanTarget = window.getCleanName(window.targetWallName);
        let targetIndex = -1;

        for (let i = 0; i < targetModel.count; i++) {
            let fname = targetModel.get(i).fileName || "";
            let isVid = fname.startsWith("000_");
            
            if (checkItemMatchesFilter(fname, isVid, window.cacheVersion, window.currentFilter)) {
                if (firstValidIndex === -1) {
                    firstValidIndex = i;
                }
                lastValidIndex = i;
                
                if (cleanTarget !== "" && window.getCleanName(fname) === cleanTarget) {
                    targetIndex = i;
                }
            }
        }

        let indexToFocus = -1;

        if (targetIndex !== -1) {
             indexToFocus = targetIndex;
        } else if (window.jumpToLastOnFilterChange && lastValidIndex !== -1) {
            indexToFocus = lastValidIndex;
        } else if (firstValidIndex !== -1) {
            indexToFocus = firstValidIndex;
        }

        window.jumpToLastOnFilterChange = false;
        
        if (indexToFocus !== -1) {
            window.executeFocusRestore(indexToFocus, false, forceSnap === true);
        }
        
        window.updateVisibleCount();
    }

    onCurrentFilterChanged: {
        window.isFilterAnimating = true;
        filterAnimationTimer.restart();
        window.isModelChanging = true; 
        let returningFromSearch = (window._lastFilter === "Search" && window.currentFilter !== "Search");
        window._lastFilter = window.currentFilter;
        
        if (returningFromSearch) {
             window.searchIndexRestored = false;
        }
        
        Qt.callLater(() => {
            if (window.currentFilter === "Search") {
                searchInput.forceActiveFocus()
                if (window.hasSearched) {
                    window.searchIndexRestored = false; 
                    window.trySearchFocus();
                }
            } else {
                view.forceActiveFocus()
                window.applyFilters(returningFromSearch);
            }
            window.isModelChanging = false;
        });
    }

    // -------------------------------------------------------------------------
    // SHORTCUTS
    // -------------------------------------------------------------------------
    Shortcut { 
        sequence: "Left"; 
        enabled: !window.isScrollingBlocked && !window.isApplying
        onActivated: window.stepToNextValidIndex(-1) 
    }
    Shortcut { 
        sequence: "Right"; 
        enabled: !window.isScrollingBlocked && !window.isApplying
        onActivated: window.stepToNextValidIndex(1) 
    }
    
    Shortcut { 
        sequence: "Return"
        // Bind the lock firmly to the shortcut to stop multiple keyboard fires
        enabled: !searchInput.activeFocus && !window.isScrollingBlocked && !window.isApplying
        onActivated: { 
            let targetModel = window.getModelForFilter(window.currentFilter);
            if (view.currentIndex >= 0 && view.currentIndex < targetModel.count) {
                let fname = targetModel.get(view.currentIndex).fileName;
                if (fname) {
                    let isVid = String(fname).startsWith("000_");
                    window.applyWallpaper(String(fname), isVid);
                }
            }
        } 
    }
    
    Shortcut { sequence: "Escape"; enabled: !window.isApplying; onActivated: { if (window.currentFilter === "Search") { window.currentFilter = "All"; } } }
    Shortcut { sequence: "Tab"; enabled: !window.isApplying; onActivated: window.cycleFilter(1) }
    Shortcut { sequence: "Backtab"; enabled: !window.isApplying; onActivated: window.cycleFilter(-1) }

    // -------------------------------------------------------------------------
    // CONTENT & DUAL MODELS
    // -------------------------------------------------------------------------
    ListModel { id: localProxyModel }
    
    readonly property var activeModel: localProxyModel

    FolderListModel {
        id: localFolderModel
        folder: window.thumbDir
        nameFilters: ["*.jpg", "*.jpeg", "*.png", "*.webp", "*.gif", "*.mp4", "*.mkv", "*.mov", "*.webm"]
        showDirs: false
        sortField: FolderListModel.Name 
        
        onCountChanged: window.syncLocalModel()
        onStatusChanged: { if (status === FolderListModel.Ready) window.syncLocalModel() }
    }

    function syncLocalModel() {
        let startIdx = localProxyModel.count;
        let endIdx = localFolderModel.count;
        
        if (endIdx < startIdx) {
            window.isModelChanging = true;
            localProxyModel.clear();
            startIdx = 0;
            window.isModelChanging = false;
        }

        for (let i = startIdx; i < endIdx; i++) {
            let fn = localFolderModel.get(i, "fileName");
            let fu = localFolderModel.get(i, "fileUrl");
            if (fn !== undefined) {
                localProxyModel.append({ "fileName": fn, "fileUrl": String(fu) });
            }
        }

        if (window.currentFilter !== "Search") window.updateVisibleCount();
        
        if (!window.initialFocusSet && window.currentFilter !== "Search" && localProxyModel.count > 0) {
            window.tryFocus();
        }
    }

    ListView {
        id: view
        anchors.fill: parent
        
        opacity: window.isReady ? 1.0 : 0.0
        anchors.margins: window.isReady ? 0 : window.s(40) 
        
        Behavior on opacity { NumberAnimation { duration: window.anim(600); easing.type: Easing.OutQuart } }
        Behavior on anchors.margins { NumberAnimation { duration: window.anim(700); easing.type: Easing.OutExpo } }

        spacing: 0
        orientation: ListView.Horizontal
        clip: false 

        interactive: !window.isScrollingBlocked && !window.isApplying
        cacheBuffer: 2000

        highlightRangeMode: ListView.StrictlyEnforceRange
        preferredHighlightBegin: (width / 2) - ((window.itemWidth * 1.5 + window.spacing) / 2)
        preferredHighlightEnd: (width / 2) + ((window.itemWidth * 1.5 + window.spacing) / 2)
        
        highlightMoveDuration: window.initialFocusSet ? window.anim(500) : 0
        focus: true
        
        onCurrentIndexChanged: {
            window.isItemAnimating = true;
            itemAnimationTimer.restart();

            if (window.currentFilter !== "Search") return;
            
            if (!window.isModelChanging && window.hasSearched && window.searchIndexRestored) {
                if (currentIndex >= 0 && currentIndex < localProxyModel.count) {
                    let fname = localProxyModel.get(currentIndex).fileName;
                    if (fname !== undefined && fname !== "") {
                        window.lastSearchName = String(fname);
                        searchState.lastName = String(fname);
                    }
                }
            }
        }
        
        add: Transition {
            enabled: window.initialFocusSet
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: window.anim(400); easing.type: Easing.OutCubic }
                NumberAnimation { property: "scale"; from: 0.5; to: 1; duration: window.anim(400); easing.type: Easing.OutBack }
            }
        }
        addDisplaced: Transition {
            enabled: window.initialFocusSet
            NumberAnimation { property: "x"; duration: window.anim(400); easing.type: Easing.OutCubic }
        }

        header: Item { width: Math.max(0, (view.width / 2) - ((window.itemWidth * 1.5) / 2)) }
        footer: Item { width: Math.max(0, (view.width / 2) - ((window.itemWidth * 1.5) / 2)) }

        model: window.activeModel

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.NoButton 

            onWheel: (wheel) => {
                if (window.isScrollingBlocked || window.isApplying) {
                    wheel.accepted = true;
                    return;
                }

                if (scrollThrottle.running) {
                   wheel.accepted = true
                   return
                }

                let dx = wheel.angleDelta.x
                let dy = wheel.angleDelta.y
                let delta = Math.abs(dx) > Math.abs(dy) ? dx : dy

                scrollAccum += delta

                if (Math.abs(scrollAccum) >= scrollThreshold) {
                    window.stepToNextValidIndex(scrollAccum > 0 ? -1 : 1)
                    scrollAccum = 0
                    scrollThrottle.start()
                }

                wheel.accepted = true
            }        
        }

        delegate: Item {
            id: delegateRoot
            
            readonly property string safeFileName: fileName !== undefined ? String(fileName) : ""
            
            readonly property bool isCurrent: ListView.isCurrentItem && !window.isScrollingBlocked
            readonly property bool isFakeSelected: window.isScrollingBlocked && index === 0
            readonly property bool isVisuallyEnlarged: isCurrent || isFakeSelected
            
            readonly property bool isVideo: safeFileName.startsWith("000_")
            readonly property bool matchesFilter: window.checkItemMatchesFilter(safeFileName, isVideo, window.cacheVersion, window.currentFilter)
            
            readonly property real targetWidth: isVisuallyEnlarged ? (window.itemWidth * 1.5) : (window.itemWidth * 0.5)
            readonly property real targetHeight: isVisuallyEnlarged ? (window.itemHeight + window.s(30)) : window.itemHeight 
            
            property bool isPlayingVideo: false
            property real pressScale: 1.0

            Timer {
                id: videoPlayTimer
                interval: 250
                running: delegateRoot.isVisuallyEnlarged && delegateRoot.isVideo && !window.isScrollingBlocked && !window.isFilterAnimating && !window.isItemAnimating
                onTriggered: {
                    if (delegateRoot.isVisuallyEnlarged && delegateRoot.isVideo) {
                        delegateRoot.isPlayingVideo = true;
                        previewPlayer.play();
                    }
                }
            }

            onIsVisuallyEnlargedChanged: {
                if (!isVisuallyEnlarged) {
                    isPlayingVideo = false;
                    videoPlayTimer.stop();
                    previewPlayer.stop();
                }
            }
            
            width: matchesFilter ? (targetWidth + window.spacing) : 0
            visible: width > 0.1 || opacity > 0.01
            opacity: matchesFilter ? (isVisuallyEnlarged ? 1.0 : 0.6) : 0.0
            
            scale: matchesFilter ? 1.0 : 0.5

            height: matchesFilter ? targetHeight : 0
            anchors.verticalCenter: parent ? parent.verticalCenter : undefined
            anchors.verticalCenterOffset: parent ? window.s(0) : 0 

            z: isVisuallyEnlarged ? 10 : 1
            
            Timer {
                id: clickPulseReset
                interval: 120
                onTriggered: delegateRoot.pressScale = 1.0
            }

            Behavior on scale { enabled: window.initialFocusSet; NumberAnimation { duration: window.anim(500); easing.type: Easing.InOutQuad } }
            Behavior on width { enabled: window.initialFocusSet; NumberAnimation { duration: window.anim(500); easing.type: Easing.InOutQuad } }
            Behavior on height { enabled: window.initialFocusSet; NumberAnimation { duration: window.anim(500); easing.type: Easing.InOutQuad } } 
            Behavior on opacity { enabled: window.initialFocusSet; NumberAnimation { duration: window.anim(500); easing.type: Easing.InOutQuad } }

            Item {
                anchors.centerIn: parent
                anchors.horizontalCenterOffset: ((window.itemHeight - height) / 2) * window.skewFactor
                
                width: parent.width > 0 ? parent.width * (targetWidth / (targetWidth + window.spacing)) : 0
                height: parent.height
                scale: delegateRoot.pressScale

                Behavior on scale {
                    NumberAnimation {
                        duration: window.anim(180)
                        easing.type: Easing.OutBack
                    }
                }

                transform: Matrix4x4 {
                    property real s: window.skewFactor
                    matrix: Qt.matrix4x4(1, s, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
                }
                
                MouseArea {
                    anchors.fill: parent
                    // Lock inputs completely on the delegate as well
                    enabled: delegateRoot.matchesFilter && !window.isScrollingBlocked && !window.isApplying
                    onClicked: {
                        delegateRoot.pressScale = 0.94
                        clickPulseReset.start()
                        view.currentIndex = index
                        window.applyWallpaper(delegateRoot.safeFileName, delegateRoot.isVideo)
                    }
                }

                Image {
                    anchors.fill: parent
                    source: fileUrl !== undefined ? fileUrl : ""
                    sourceSize: Qt.size(1, 1)
                    fillMode: Image.Stretch
                    visible: true 
                    asynchronous: true
                }

                Item {
                    anchors.fill: parent
                    anchors.margins: window.borderWidth 
                    Rectangle { anchors.fill: parent; color: "black" }
                    clip: true

                    Image {
                        anchors.centerIn: parent
                        anchors.horizontalCenterOffset: window.s(-50) 
                        width: (window.itemWidth * 1.5) + ((window.itemHeight + window.s(30)) * Math.abs(window.skewFactor)) + window.s(50)
                        height: window.itemHeight + window.s(30)
                        fillMode: Image.PreserveAspectCrop
                        source: fileUrl !== undefined ? fileUrl : ""
                        asynchronous: true

                        transform: Matrix4x4 {
                            property real s: -window.skewFactor
                            matrix: Qt.matrix4x4(1, s, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
                        }
                    }
                    
                    MediaPlayer {
                        id: previewPlayer
                        source: delegateRoot.isPlayingVideo
                            ? "file://" + window.srcDir + "/" +
                              window.getSourceFileName(
                                  delegateRoot.safeFileName,
                                  true
                              )
                            : ""
                        audioOutput: AudioOutput { muted: true }
                        videoOutput: previewOutput
                        loops: MediaPlayer.Infinite
                    }

                    VideoOutput {
                        id: previewOutput
                        anchors.centerIn: parent
                        anchors.horizontalCenterOffset: window.s(-50) 
                        width: (window.itemWidth * 1.5) + ((window.itemHeight + window.s(30)) * Math.abs(window.skewFactor)) + window.s(50)
                        height: window.itemHeight + window.s(30)
                        fillMode: VideoOutput.PreserveAspectCrop
                        visible: delegateRoot.isPlayingVideo && previewPlayer.playbackState === MediaPlayer.PlayingState

                        transform: Matrix4x4 {
                            property real s: -window.skewFactor
                            matrix: Qt.matrix4x4(1, s, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
                        }
                    }
                    
                    Rectangle {
                        visible: delegateRoot.isVideo && (!delegateRoot.isPlayingVideo || previewPlayer.playbackState !== MediaPlayer.PlayingState)
                        anchors.bottom: parent.bottom
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.margins: window.s(10)
                        width: window.s(32)
                        height: window.s(32)
                        radius: window.s(6)
                        color: "#60000000" 
                        transform: Matrix4x4 {
                            property real s: -window.skewFactor
                            matrix: Qt.matrix4x4(1, s, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
                        }
                        
                        Canvas {
                            anchors.fill: parent
                            anchors.margins: window.s(8)
                            property real scaleTrigger: window.s(1)
                            onScaleTriggerChanged: requestPaint()
                            onPaint: {
                                var ctx = getContext("2d");
                                var s = window.s;
                                ctx.reset();
                                ctx.fillStyle = "#EEFFFFFF"; 
                                ctx.beginPath();
                                ctx.moveTo(s(4), 0);
                                ctx.lineTo(s(14), s(8));
                                ctx.lineTo(s(4), s(16));
                                ctx.closePath();
                                ctx.fill();
                            }
                        }
                    }
                }
            }
        }
    }

    // -------------------------------------------------------------------------
    // FLOATING FILTER BAR & INLINE NOTIFICATION DRAWER
    // -------------------------------------------------------------------------
    Rectangle {
        id: filterBarBackground
        anchors.bottom: parent.bottom
        
        anchors.bottomMargin: window.isReady ? window.s(6) : window.s(-100) 
        opacity: window.isReady ? 1.0 : 0.0
        Behavior on anchors.bottomMargin { NumberAnimation { duration: window.anim(600); easing.type: Easing.OutExpo } }
        Behavior on opacity { NumberAnimation { duration: window.anim(500); easing.type: Easing.OutCubic } }

        anchors.horizontalCenter: parent.horizontalCenter
        z: 20
        height: window.s(56)
        width: filterRow.width + window.s(24)
        radius: window.s(14) 
        
        color: Qt.rgba(_theme.mantle.r, _theme.mantle.g, _theme.mantle.b, 0.90)
        border.color: Qt.rgba(_theme.surface2.r, _theme.surface2.g, _theme.surface2.b, 0.8)
        border.width: 1

        Row {
            id: filterRow
            anchors.centerIn: parent
            spacing: window.s(12)

            Rectangle {
                id: notifDrawer
                height: window.s(44)
                property real paddingLeft: window.showSpinner ? window.s(40) : window.s(16)
                property real targetWidth: window.showNotification ? Math.min(notifTextDrawer.implicitWidth + paddingLeft + window.s(20), window.s(300)) : 0
                width: targetWidth
                visible: width > 0.1 
                radius: window.s(10) 
                clip: true
                
                color: window.showNotification ? Qt.rgba(_theme.surface2.r, _theme.surface2.g, _theme.surface2.b, 0.5) : "transparent"
                border.color: window.showNotification ? Qt.rgba(_theme.surface1.r, _theme.surface1.g, _theme.surface1.b, 0.8) : "transparent"
                border.width: 1

                Behavior on width { 
                    NumberAnimation { duration: window.anim(600); easing.type: Easing.OutBack; easing.overshoot: 0.5 } 
                }
                Behavior on color { ColorAnimation { duration: window.anim(400) } }
                Behavior on border.color { ColorAnimation { duration: window.anim(400) } }

                Item {
                    visible: window.showSpinner
                    width: window.s(44)
                    height: window.s(44)
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter

                    Canvas {
                        id: notifSpinner
                        width: window.s(14)
                        height: window.s(14)
                        anchors.centerIn: parent
                        property real scaleTrigger: window.s(1)
                        onScaleTriggerChanged: requestPaint()

                        onPaint: {
                            var ctx = getContext("2d");
                            var s = window.s;
                            ctx.reset();
                            ctx.lineWidth = s(2);
                            ctx.strokeStyle = Qt.rgba(_theme.text.r, _theme.text.g, _theme.text.b, 0.3);
                            ctx.beginPath();
                            ctx.arc(s(7), s(7), s(5), 0, Math.PI * 2);
                            ctx.stroke();
                            
                            ctx.strokeStyle = Qt.rgba(_theme.text.r, _theme.text.g, _theme.text.b, 0.9);
                            ctx.beginPath();
                            ctx.arc(s(7), s(7), s(5), 0, Math.PI * 0.5);
                            ctx.stroke();
                        }
                        RotationAnimation on rotation {
                            loops: Animation.Infinite
                            from: 0; to: 360
                            duration: window.anim(800)
                            running: window.showSpinner && window.showNotification
                        }
                    }
                }

                Text {
                    id: notifTextDrawer
                    anchors.left: parent.left
                    anchors.leftMargin: window.showSpinner ? window.s(40) : window.s(16)
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.min(implicitWidth, window.s(300) - anchors.leftMargin - window.s(16))
                    text: window.currentNotification
                    
                    color: _theme.text
                    font.family: "JetBrains Mono"
                    font.pixelSize: window.s(14)
                    font.bold: true
                    elide: Text.ElideRight

                    opacity: window.showNotification ? 0.9 : 0.0
                    Behavior on opacity { NumberAnimation { duration: window.anim(400); easing.type: Easing.OutQuad } }
                    Behavior on anchors.leftMargin { 
                        NumberAnimation { duration: window.anim(600); easing.type: Easing.OutBack; easing.overshoot: 0.5 } 
                    }
                }
            }

            Repeater {
                model: window.filterData

                delegate: Item {
                    visible: modelData.name !== "Search"
                    width: !visible ? 0 : ((modelData.name === "Video" || modelData.name === "All") ? window.s(44) : (modelData.hex === "" ? filterText.contentWidth + window.s(24) : window.s(36)))
                    height: !visible ? 0 : window.s(36)
                    anchors.verticalCenter: parent.verticalCenter
                    
                    Rectangle {
                        anchors.fill: parent
                        radius: window.s(10) 
                        color: modelData.hex === "" 
                                ? (window.currentFilter === modelData.name ? _theme.surface2 : "transparent") 
                                : modelData.hex
                        
                        border.color: window.currentFilter === modelData.name ? _theme.text : Qt.rgba(_theme.surface1.r, _theme.surface1.g, _theme.surface1.b, 0.6)
                        border.width: window.currentFilter === modelData.name ? window.s(2) : 1
                        scale: window.currentFilter === modelData.name ? 1.15 : (filterMouse.containsMouse ? 1.08 : 1.0)
                        
                        Behavior on scale { NumberAnimation { duration: window.anim(400); easing.type: Easing.OutBack; easing.overshoot: 1.2 } }
                        Behavior on border.color { ColorAnimation { duration: 300 } }

                        Text {
                            id: filterText
                            visible: modelData.hex === "" && modelData.name !== "Video" && modelData.name !== "All"
                            text: modelData.label
                            anchors.centerIn: parent
                            color: window.currentFilter === modelData.name ? _theme.text : Qt.rgba(_theme.text.r, _theme.text.g, _theme.text.b, 0.7)
                            font.family: "JetBrains Mono"
                            font.pixelSize: window.s(14)
                            font.bold: window.currentFilter === modelData.name
                            Behavior on color { ColorAnimation { duration: window.anim(400); easing.type: Easing.OutQuart } }
                        }

                        Canvas {
                            visible: modelData.name === "Video"
                            width: window.s(14); height: window.s(16)
                            anchors.centerIn: parent
                            anchors.horizontalCenterOffset: window.s(2) 
                            property string activeColor: window.currentFilter === modelData.name ? _theme.text : Qt.rgba(_theme.text.r, _theme.text.g, _theme.text.b, 0.7)
                            onActiveColorChanged: requestPaint()
                            property real scaleTrigger: window.s(1)
                            onScaleTriggerChanged: requestPaint()

                            onPaint: {
                                var ctx = getContext("2d");
                                var s = window.s;
                                ctx.reset();
                                ctx.fillStyle = activeColor; 
                                ctx.beginPath();
                                ctx.moveTo(0, 0);
                                ctx.lineTo(s(14), s(8));
                                ctx.lineTo(0, s(16));
                                ctx.closePath();
                                ctx.fill();
                            }
                        }

                        Canvas {
                            visible: modelData.name === "All"
                            width: window.s(14); height: window.s(14)
                            anchors.centerIn: parent
                            property string activeColor: window.currentFilter === modelData.name ? _theme.text : Qt.rgba(_theme.text.r, _theme.text.g, _theme.text.b, 0.7)
                            onActiveColorChanged: requestPaint()
                            property real scaleTrigger: window.s(1)
                            onScaleTriggerChanged: requestPaint()

                            onPaint: {
                                var ctx = getContext("2d");
                                var s = window.s;
                                ctx.reset();
                                ctx.fillStyle = activeColor;
                                ctx.fillRect(0, 0, s(6), s(6));
                                ctx.fillRect(s(8), 0, s(6), s(6));
                                ctx.fillRect(0, s(8), s(6), s(6));
                                ctx.fillRect(s(8), s(8), s(6), s(6));
                            }
                        }
                    }

                    MouseArea {
                        id: filterMouse
                        anchors.fill: parent
                        hoverEnabled: true 
                        enabled: !window.isApplying // Lock UI interaction
                        onClicked: window.currentFilter = modelData.name
                        cursorShape: Qt.PointingHandCursor
                    }
                }
            }


            Rectangle {
                id: searchBox
                height: window.s(44)
                width: window.currentFilter === "Search" ? window.s(360) : window.s(44) 
                radius: window.s(10) 
                clip: true
                
                color: window.currentFilter === "Search" ? Qt.rgba(_theme.surface2.r, _theme.surface2.g, _theme.surface2.b, 0.8) : "transparent"
                border.color: window.currentFilter === "Search" ? Qt.rgba(_theme.text.r, _theme.text.g, _theme.text.b, 0.5) : Qt.rgba(_theme.surface1.r, _theme.surface1.g, _theme.surface1.b, 0.6)
                border.width: window.currentFilter === "Search" ? window.s(2) : 1
                
                Behavior on width { NumberAnimation { duration: window.anim(600); easing.type: Easing.OutBack; easing.overshoot: 0.5 } }
                Behavior on color { ColorAnimation { duration: window.anim(400); easing.type: Easing.OutQuart } }
                Behavior on border.color { ColorAnimation { duration: window.anim(400) } }

                MouseArea {
                    id: searchMouseArea
                    anchors.fill: parent
                    hoverEnabled: true 
                    enabled: !window.isApplying // Lock UI interaction
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        if (window.currentFilter !== "Search") {
                            window.currentFilter = "Search"
                        } else {
                            searchInput.forceActiveFocus()
                        }
                    }
                }

                Canvas {
                    id: searchIcon
                    width: window.s(44)
                    height: window.s(44)
                    anchors.left: parent.left
                    anchors.leftMargin: window.currentFilter === "Search" ? window.s(5) : 0 
                    anchors.verticalCenter: parent.verticalCenter
                    Behavior on anchors.leftMargin { NumberAnimation { duration: window.anim(500); easing.type: Easing.OutExpo } }
                    property string activeColor: window.currentFilter === "Search" ? _theme.text : (searchMouseArea.containsMouse ? _theme.text : Qt.rgba(_theme.text.r, _theme.text.g, _theme.text.b, 0.7))
                    onActiveColorChanged: requestPaint()
                    property real scaleTrigger: window.s(1)
                    onScaleTriggerChanged: requestPaint()

                    onPaint: {
                        var ctx = getContext("2d");
                        var s = window.s;
                        ctx.reset();
                        ctx.lineWidth = s(3); 
                        ctx.strokeStyle = activeColor;
                        ctx.beginPath();
                        ctx.arc(s(18), s(18), s(7), 0, Math.PI * 2);
                        ctx.stroke();
                        ctx.beginPath();
                        ctx.moveTo(s(23), s(23));
                        ctx.lineTo(s(31), s(31));
                        ctx.stroke();
                    }
                }

                TextInput {
                    id: searchInput
                    anchors.left: searchIcon.right
                   anchors.right: parent.right
                   anchors.rightMargin: window.s(12)
                    anchors.verticalCenter: parent.verticalCenter
                    
                    opacity: window.currentFilter === "Search" ? 1.0 : 0.0
                    visible: opacity > 0
                    Behavior on opacity { NumberAnimation { duration: window.anim(400); easing.type: Easing.OutQuad } }
                    
                    color: _theme.text
                    font.family: "JetBrains Mono"
                    font.pixelSize: window.s(16) 
                    clip: true
                    
                    onTextEdited: {
                        if (window.isSearchingOnline) {
                            onlineSearchProcess.running = false
                            window.isSearchingOnline = false
                        }

                        if (window.isOnlineSearch) {
                            window.isOnlineSearch = false
                            localProxyModel.clear()
                            window.syncLocalModel()
                        }

                        window.onlineSearchError = ""
                        window.searchQuery = text.trim().toLowerCase()

                        window.currentFilter = "Search"
                        window.hasSearched = true
                        window.isOnlineSearch = false
                        window.isSearchPaused = true

                        searchState.searched = true
                        searchState.query = window.searchQuery

                        view.currentIndex = 0
                        window.updateVisibleCount()
                        window.applyFilters(true)
                    }
                    
                    onAccepted: {
                        window.triggerLocalSearch();
                        searchInput.focus = false; 
                        view.forceActiveFocus();
                    }
                }

            }
        }
    }

    Component.onCompleted: {
        Quickshell.execDetached(["bash", "-c", "mkdir -p '" + decodeURIComponent(window.searchDir.replace("file://", "")) + "'"]);
        
        if (searchState.searched) {
            searchInput.text = searchState.query;
            window.searchQuery = searchState.query;
            window.hasSearched = true;
            window.lastSearchName = searchState.lastName;
            window.isSearchPaused = true; 
        }

        view.forceActiveFocus();
        window.processMarkers();
        window.triggerColorExtraction();
    }

    Component.onDestruction: {
        if (window.hasSearched) {
            searchState.query = searchInput.text;
            searchState.searched = window.hasSearched;
            searchState.lastName = window.lastSearchName;
        }
    }
}
