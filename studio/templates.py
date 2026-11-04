"""Шаблоны новых файлов и пример решения (Unity + PyQt)."""
import os

PYQT_WINDOW = '''import sys

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QLabel, QPushButton)


class {name}(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("{title}")
        self.resize(480, 320)

        central = QWidget()
        layout = QVBoxLayout(central)
        self.label = QLabel("Привет!")
        self.button = QPushButton("Нажми меня")
        self.button.clicked.connect(self.on_button_clicked)
        layout.addWidget(self.label)
        layout.addWidget(self.button)
        self.setCentralWidget(central)

    def on_button_clicked(self):
        self.label.setText("Кнопка нажата")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = {name}()
    window.show()
    sys.exit(app.exec())
'''

PYQT_DIALOG = '''from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
                             QVBoxLayout)


class {name}(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("{title}")
        self.name_edit = QLineEdit()

        form = QFormLayout()
        form.addRow("Имя:", self.name_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def value(self):
        return self.name_edit.text()
'''

PYQT_WIDGET = '''from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QSlider
from PyQt6.QtCore import Qt


class {name}(QWidget):
    valueChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.label = QLabel("0")
        self.slider.valueChanged.connect(self._on_value)
        layout = QHBoxLayout(self)
        layout.addWidget(self.slider)
        layout.addWidget(self.label)

    def _on_value(self, value):
        self.label.setText(str(value))
        self.valueChanged.emit(value)
'''

PYQT_UI = '''<?xml version="1.0" encoding="UTF-8"?>
<ui version="4.0">
 <class>{name}</class>
 <widget class="QWidget" name="{name}">
  <property name="geometry">
   <rect><x>0</x><y>0</y><width>360</width><height>200</height></rect>
  </property>
  <property name="windowTitle">
   <string>{title}</string>
  </property>
  <layout class="QVBoxLayout" name="verticalLayout">
   <item>
    <widget class="QLabel" name="label">
     <property name="text"><string>Введите текст:</string></property>
    </widget>
   </item>
   <item>
    <widget class="QLineEdit" name="lineEdit"/>
   </item>
   <item>
    <widget class="QPushButton" name="okButton">
     <property name="text"><string>OK</string></property>
    </widget>
   </item>
  </layout>
 </widget>
</ui>
'''

PY_EMPTY = '''"""{title}"""


def main():
    print("Привет из {name}!")


if __name__ == "__main__":
    main()
'''

UNITY_MONO = '''using UnityEngine;

public class {name} : MonoBehaviour
{{
    [SerializeField] private float speed = 5f;

    void Start()
    {{

    }}

    void Update()
    {{

    }}
}}
'''

UNITY_SO = '''using UnityEngine;

[CreateAssetMenu(fileName = "{name}", menuName = "Data/{name}")]
public class {name} : ScriptableObject
{{
    public string displayName;
    public int value = 1;
}}
'''

UNITY_EDITOR = '''using UnityEditor;
using UnityEngine;

public class {name} : EditorWindow
{{
    private string note = "";

    [MenuItem("Tools/{title}")]
    public static void ShowWindow()
    {{
        GetWindow<{name}>("{title}");
    }}

    void OnGUI()
    {{
        GUILayout.Label("{title}");
        note = EditorGUILayout.TextField("Заметка", note);
        if (GUILayout.Button("Готово"))
        {{
            Debug.Log(note);
        }}
    }}
}}
'''

CS_CLASS = '''using System;

public class {name}
{{
    public {name}()
    {{
    }}
}}
'''

CS_INTERFACE = '''public interface {name}
{{
    void Execute();
}}
'''

# (подпись, расширение, текст, куда класть: "unity", "unity_editor", "python" или None)
TEMPLATES = [
    ("Окно PyQt (QMainWindow)", ".py", PYQT_WINDOW, "python"),
    ("Диалог PyQt (QDialog)", ".py", PYQT_DIALOG, "python"),
    ("Виджет PyQt (QWidget)", ".py", PYQT_WIDGET, "python"),
    ("Форма Qt Designer (.ui)", ".ui", PYQT_UI, "python"),
    ("Файл Python", ".py", PY_EMPTY, "python"),
    ("Скрипт Unity (MonoBehaviour)", ".cs", UNITY_MONO, "unity"),
    ("Данные Unity (ScriptableObject)", ".cs", UNITY_SO, "unity"),
    ("Окно редактора Unity (EditorWindow)", ".cs", UNITY_EDITOR, "unity_editor"),
    ("Класс C#", ".cs", CS_CLASS, "unity"),
    ("Интерфейс C#", ".cs", CS_INTERFACE, "unity"),
    ("Текстовый файл", ".txt", "", None),
    ("Markdown", ".md", "# {title}\n", None),
]


def render(template, name):
    cls = "".join(p[:1].upper() + p[1:] for p in name.replace("-", "_").split("_") if p) or "NewFile"
    if not cls.isidentifier():
        cls = "NewFile"
    title = name.replace("_", " ").strip() or cls
    return template.format(name=cls, title=title)


# ====================================================================== пример решения
SAMPLE = {
    "README.md": """# Пример решения

Два проекта в одной папке:

- **PyQtApp** — настольное приложение на Python и PyQt6. Запуск: F5.
- **UnityGame** — скрипты C# для Unity. Откройте проект кнопкой «Unity» на панели.

Подсказки кода: начните печатать или нажмите Ctrl+Пробел.
""",
    "PyQtApp/main.py": """import sys

from PyQt6.QtWidgets import QApplication

from main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
""",
    "PyQtApp/main_window.py": '''from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QPushButton, QLineEdit, QListWidget)


class MainWindow(QMainWindow):
    """Главное окно: список задач с таймером."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Список задач")
        self.resize(420, 360)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Новая задача…")
        self.add_button = QPushButton("Добавить")
        self.tasks = QListWidget()
        self.status = QLabel("Задач: 0")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight)

        row = QHBoxLayout()
        row.addWidget(self.input)
        row.addWidget(self.add_button)

        layout = QVBoxLayout()
        layout.addLayout(row)
        layout.addWidget(self.tasks)
        layout.addWidget(self.status)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.add_button.clicked.connect(self.add_task)
        self.input.returnPressed.connect(self.add_task)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_status)
        self.timer.start(1000)

    def add_task(self):
        text = self.input.text().strip()
        if text:
            self.tasks.addItem(text)
            self.input.clear()
            self.update_status()

    def update_status(self):
        self.status.setText(f"Задач: {self.tasks.count()}")
''',
    "PyQtApp/ui/settings.ui": PYQT_UI.format(name="SettingsForm", title="Настройки"),
    "PyQtApp/requirements.txt": "PyQt6>=6.5\n",
    "UnityGame/Assets/Scripts/PlayerController.cs": '''using UnityEngine;

[RequireComponent(typeof(Rigidbody))]
public class PlayerController : MonoBehaviour
{
    [Header("Движение")]
    [SerializeField] private float speed = 6f;
    [SerializeField] private float jumpForce = 5f;

    private Rigidbody rb;
    private bool isGrounded;

    void Awake()
    {
        rb = GetComponent<Rigidbody>();
    }

    void Update()
    {
        float h = Input.GetAxis("Horizontal");
        float v = Input.GetAxis("Vertical");
        Vector3 move = new Vector3(h, 0f, v) * speed * Time.deltaTime;
        transform.Translate(move, Space.World);

        if (isGrounded && Input.GetKeyDown(KeyCode.Space))
        {
            rb.AddForce(Vector3.up * jumpForce, ForceMode.Impulse);
            isGrounded = false;
        }
    }

    void OnCollisionEnter(Collision collision)
    {
        if (collision.gameObject.CompareTag("Ground"))
        {
            isGrounded = true;
        }
    }
}
''',
    "UnityGame/Assets/Scripts/GameManager.cs": '''using UnityEngine;
using UnityEngine.SceneManagement;

public class GameManager : MonoBehaviour
{
    public static GameManager Instance { get; private set; }

    [SerializeField] private int score;

    void Awake()
    {
        if (Instance != null)
        {
            Destroy(gameObject);
            return;
        }
        Instance = this;
        DontDestroyOnLoad(gameObject);
    }

    public void AddScore(int amount)
    {
        score += amount;
        Debug.Log("Очки: " + score);
    }

    public void Restart()
    {
        SceneManager.LoadScene(SceneManager.GetActiveScene().name);
    }
}
''',
    "UnityGame/Assets/Scenes/README.txt": "Сцены Unity (.unity) появятся здесь после открытия проекта в Unity.\n",
    "UnityGame/Packages/manifest.json": """{
  "dependencies": {
    "com.unity.ugui": "2.0.0"
  }
}
""",
    "UnityGame/ProjectSettings/ProjectVersion.txt": "m_EditorVersion: 6000.3.4f1\n",
}


def ensure_sample(root):
    """Создаёт пример решения, если его ещё нет. Существующие файлы не трогает."""
    for rel, text in SAMPLE.items():
        path = os.path.join(root, *rel.split("/"))
        if os.path.exists(path):
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return root
