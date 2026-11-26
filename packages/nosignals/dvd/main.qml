import QtQuick 2.4

Item {
    id: root
    property bool started: false
    property real k: height / 1080
    property real speed: 140 * k
    property real travel: 0
    property real startX: 0
    property real startY: 0
    property real roomX: Math.max(1, width - mark.width)
    property real roomY: Math.max(1, height - mark.height)
    property real pathX: startX + travel * speed
    property real pathY: startY + travel * speed
    property int legX: Math.floor(pathX / roomX)
    property int legY: Math.floor(pathY / roomY)
    property real hue: Math.random()
    property bool late: false
    property bool hidesLg: started || (!late && logo.status !== Image.Error)

    function fold(p, room) { var r = p % (2 * room); return r < room ? r : 2 * room - r; }
    function recolour() { hue = (hue + 0.17 + Math.random() * 0.66) % 1; }
    function begin() {
        if (started || width <= 0 || height <= 0 || logo.status !== Image.Ready) return;
        startX = Math.random() * 2 * roomX;
        startY = Math.random() * 2 * roomY;
        started = true;
        run.restart();
    }

    onLegXChanged: if (started) recolour()
    onLegYChanged: if (started) recolour()
    onWidthChanged: begin()
    onHeightChanged: begin()
    Component.onCompleted: begin()

    Timer { interval: 3000; running: !root.started; onTriggered: root.late = true }
    NumberAnimation { id: run; target: root; property: "travel"; from: 0; to: 2000000; duration: 2000000000 }

    Rectangle { anchors.fill: parent; color: "black" }

    Image {
        id: logo
        source: "dvd.png"
        visible: false
        asynchronous: true
        smooth: true
        onStatusChanged: if (status === Image.Ready) root.begin()
    }

    ShaderEffect {
        id: mark
        property variant source: logo
        property color tint: Qt.hsla(root.hue, 1, 0.55, 1)
        width: Math.round(400 * root.k)
        height: Math.round(width * logo.implicitHeight / Math.max(1, logo.implicitWidth))
        x: root.fold(root.pathX, root.roomX)
        y: root.fold(root.pathY, root.roomY)
        visible: root.started
        fragmentShader: "varying highp vec2 qt_TexCoord0; uniform sampler2D source; uniform lowp vec4 tint; uniform lowp float qt_Opacity; void main() { lowp vec4 p = texture2D(source, qt_TexCoord0); gl_FragColor = vec4(tint.rgb * dot(p.rgb, vec3(0.299, 0.587, 0.114)), p.a) * qt_Opacity; }"
    }
}
