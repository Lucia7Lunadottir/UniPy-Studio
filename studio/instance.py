"""Project-scoped local IPC for launches from Unity."""
import hashlib
import json
import os
from PyQt6.QtCore import QObject, QLockFile, QStandardPaths
from PyQt6.QtNetwork import QLocalServer, QLocalSocket


class ProjectInstance(QObject):
    def __init__(self, root):
        super().__init__()
        key = hashlib.sha256((str(os.getuid()) + os.path.realpath(root)).encode()).hexdigest()[:32]
        self.name = "unipy-" + key
        runtime = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.RuntimeLocation)
        self.lock = QLockFile(os.path.join(runtime, self.name + ".lock"))
        self.server = QLocalServer(self)
        self.clients = {}
        self.owner = False

    def forward(self, request):
        self.owner = self.lock.tryLock(0)
        if self.owner:
            return False
        socket = QLocalSocket()
        # A first instance may still be constructing its window.
        for _ in range(30):
            socket.connectToServer(self.name)
            if socket.waitForConnected(100):
                socket.write(json.dumps(request).encode() + b"\n")
                socket.waitForBytesWritten(1000)
                if socket.waitForReadyRead(3000) and bytes(socket.readAll()) == b"OK\n":
                    return True
                raise RuntimeError("UniPy Studio: нет подтверждения открытия файла")
            socket.abort()
            from PyQt6.QtCore import QThread
            QThread.msleep(100)
        raise RuntimeError("UniPy Studio: окно проекта не отвечает; повторите запуск")

    def listen(self, callback):
        if not self.owner:
            return
        QLocalServer.removeServer(self.name)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.callback = callback
        self.server.newConnection.connect(self.accept)
        if not self.server.listen(self.name):
            raise RuntimeError(self.server.errorString())

    def accept(self):
        while self.server.hasPendingConnections():
            client = self.server.nextPendingConnection()
            self.clients[client] = bytearray()
            client.readyRead.connect(lambda c=client: self.read(c))
            client.disconnected.connect(lambda c=client: self.drop(c))
            if client.bytesAvailable():
                self.read(client)

    def drop(self, client):
        self.clients.pop(client, None)
        client.deleteLater()

    def read(self, client):
        data = self.clients[client]
        data.extend(bytes(client.readAll()))
        if len(data) > 65536:
            client.disconnectFromServer()
            return
        if b"\n" in data:
            try:
                self.callback(json.loads(bytes(data).split(b"\n", 1)[0]))
                client.write(b"OK\n")
                client.flush()
            except (ValueError, KeyError, TypeError):
                pass
            client.disconnectFromServer()
