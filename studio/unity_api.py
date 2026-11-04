"""Справочник Unity API для подсказок C#.

Строка члена: «вид сигнатура | описание», где вид:
  m — метод экземпляра, p — свойство экземпляра,
  sm — статический метод, sp — статическое свойство, c — значение перечисления.
"""
import re

_SPEC = {
"Object": ("Базовый класс всех объектов Unity", """
p string name | Имя объекта
m int GetInstanceID() | Уникальный номер объекта
sm Object Instantiate(Object original) | Создаёт копию объекта
sm Object Instantiate(Object original, Vector3 position, Quaternion rotation) | Создаёт копию в позиции с поворотом
sm Object Instantiate(Object original, Transform parent) | Создаёт копию внутри родителя
sm void Destroy(Object obj, float t = 0.0f) | Удаляет объект (можно с задержкой t секунд)
sm void DontDestroyOnLoad(Object target) | Не удалять объект при смене сцены
sm T FindFirstObjectByType<T>() | Находит первый объект типа T на сцене
sm T[] FindObjectsByType<T>(FindObjectsSortMode sortMode) | Находит все объекты типа T
"""),
"Component": ("Базовый класс компонентов", """
p Transform transform | Transform объекта
p GameObject gameObject | Объект, к которому прикреплён компонент
p string tag | Тег объекта
m T GetComponent<T>() | Возвращает компонент типа T на этом объекте
m T[] GetComponents<T>() | Все компоненты типа T
m T GetComponentInChildren<T>() | Компонент T на объекте или его детях
m T GetComponentInParent<T>() | Компонент T на объекте или его родителях
m bool TryGetComponent<T>(out T component) | Пытается получить компонент T
m bool CompareTag(string tag) | Проверяет тег объекта
m void SendMessage(string methodName) | Вызывает метод у всех скриптов объекта
"""),
"Behaviour": ("Компонент, который можно включать и выключать", """
p bool enabled | Включён ли компонент
p bool isActiveAndEnabled | Компонент включён и объект активен
"""),
"MonoBehaviour": ("Базовый класс для скриптов Unity", """
m Coroutine StartCoroutine(IEnumerator routine) | Запускает корутину
m void StopCoroutine(Coroutine routine) | Останавливает корутину
m void StopAllCoroutines() | Останавливает все корутины скрипта
m void Invoke(string methodName, float time) | Вызывает метод через time секунд
m void InvokeRepeating(string methodName, float time, float repeatRate) | Вызывает метод повторно
m void CancelInvoke() | Отменяет все Invoke
m bool IsInvoking(string methodName) | Ждёт ли метод вызова
sm void print(object message) | Выводит сообщение в консоль Unity
"""),
"GameObject": ("Объект на сцене", """
p Transform transform | Transform объекта
p bool activeSelf | Активен ли сам объект
p bool activeInHierarchy | Активен ли объект с учётом родителей
p string tag | Тег объекта
p int layer | Слой объекта
p string name | Имя объекта
p Scene scene | Сцена объекта
m void SetActive(bool value) | Включает или выключает объект
m T GetComponent<T>() | Возвращает компонент типа T
m T AddComponent<T>() | Добавляет компонент типа T
m bool TryGetComponent<T>(out T component) | Пытается получить компонент T
m T GetComponentInChildren<T>() | Компонент T на объекте или детях
m bool CompareTag(string tag) | Проверяет тег
sm GameObject Find(string name) | Находит объект по имени
sm GameObject FindWithTag(string tag) | Находит объект по тегу
sm GameObject[] FindGameObjectsWithTag(string tag) | Все объекты с тегом
sm GameObject CreatePrimitive(PrimitiveType type) | Создаёт куб, сферу и т.п.
"""),
"Transform": ("Позиция, поворот и масштаб объекта", """
p Vector3 position | Позиция в мире
p Vector3 localPosition | Позиция относительно родителя
p Quaternion rotation | Поворот в мире
p Quaternion localRotation | Поворот относительно родителя
p Vector3 eulerAngles | Поворот в градусах
p Vector3 localEulerAngles | Локальный поворот в градусах
p Vector3 localScale | Масштаб
p Vector3 forward | Направление «вперёд» (синяя ось)
p Vector3 right | Направление «вправо» (красная ось)
p Vector3 up | Направление «вверх» (зелёная ось)
p Transform parent | Родитель
p int childCount | Число детей
p GameObject gameObject | Объект этого Transform
m void Translate(Vector3 translation) | Сдвигает объект
m void Translate(float x, float y, float z) | Сдвигает объект по осям
m void Rotate(Vector3 eulers) | Поворачивает объект
m void Rotate(float xAngle, float yAngle, float zAngle) | Поворачивает объект по осям
m void RotateAround(Vector3 point, Vector3 axis, float angle) | Вращает вокруг точки
m void LookAt(Transform target) | Поворачивает к цели
m void LookAt(Vector3 worldPosition) | Поворачивает к точке
m void SetParent(Transform parent) | Задаёт родителя
m void SetPositionAndRotation(Vector3 position, Quaternion rotation) | Позиция и поворот сразу
m Transform GetChild(int index) | Ребёнок по номеру
m Transform Find(string name) | Ребёнок по имени
m Vector3 TransformPoint(Vector3 position) | Локальная точка → мировая
m Vector3 InverseTransformPoint(Vector3 position) | Мировая точка → локальная
"""),
"Vector3": ("Вектор или точка в 3D", """
p float x | Координата X
p float y | Координата Y
p float z | Координата Z
p float magnitude | Длина вектора
p float sqrMagnitude | Квадрат длины (быстрее)
p Vector3 normalized | Вектор длины 1
m void Normalize() | Делает длину равной 1
m void Set(float x, float y, float z) | Задаёт координаты
sp Vector3 zero | (0, 0, 0)
sp Vector3 one | (1, 1, 1)
sp Vector3 up | (0, 1, 0)
sp Vector3 down | (0, -1, 0)
sp Vector3 forward | (0, 0, 1)
sp Vector3 back | (0, 0, -1)
sp Vector3 left | (-1, 0, 0)
sp Vector3 right | (1, 0, 0)
sm float Distance(Vector3 a, Vector3 b) | Расстояние между точками
sm Vector3 Lerp(Vector3 a, Vector3 b, float t) | Плавный переход между точками
sm Vector3 MoveTowards(Vector3 current, Vector3 target, float maxDistanceDelta) | Двигает к цели
sm float Dot(Vector3 lhs, Vector3 rhs) | Скалярное произведение
sm Vector3 Cross(Vector3 lhs, Vector3 rhs) | Векторное произведение
sm float Angle(Vector3 from, Vector3 to) | Угол между векторами
sm Vector3 ClampMagnitude(Vector3 vector, float maxLength) | Ограничивает длину
sm Vector3 Reflect(Vector3 inDirection, Vector3 inNormal) | Отражение
sm Vector3 SmoothDamp(Vector3 current, Vector3 target, ref Vector3 currentVelocity, float smoothTime) | Плавное следование
"""),
"Vector2": ("Вектор или точка в 2D", """
p float x | Координата X
p float y | Координата Y
p float magnitude | Длина вектора
p Vector2 normalized | Вектор длины 1
sp Vector2 zero | (0, 0)
sp Vector2 one | (1, 1)
sp Vector2 up | (0, 1)
sp Vector2 down | (0, -1)
sp Vector2 left | (-1, 0)
sp Vector2 right | (1, 0)
sm float Distance(Vector2 a, Vector2 b) | Расстояние между точками
sm Vector2 Lerp(Vector2 a, Vector2 b, float t) | Плавный переход
sm Vector2 MoveTowards(Vector2 current, Vector2 target, float maxDistanceDelta) | Двигает к цели
"""),
"Quaternion": ("Поворот", """
p Vector3 eulerAngles | Поворот в градусах
sp Quaternion identity | Без поворота
sm Quaternion Euler(float x, float y, float z) | Поворот из углов в градусах
sm Quaternion Euler(Vector3 euler) | Поворот из углов в градусах
sm Quaternion LookRotation(Vector3 forward) | Поворот «смотреть в направлении»
sm Quaternion Lerp(Quaternion a, Quaternion b, float t) | Плавный поворот
sm Quaternion Slerp(Quaternion a, Quaternion b, float t) | Сферически плавный поворот
sm Quaternion RotateTowards(Quaternion from, Quaternion to, float maxDegreesDelta) | Поворачивает к цели
sm Quaternion AngleAxis(float angle, Vector3 axis) | Поворот вокруг оси
sm float Angle(Quaternion a, Quaternion b) | Угол между поворотами
"""),
"Rigidbody": ("Физическое тело в 3D", """
p Vector3 linearVelocity | Скорость движения
p Vector3 velocity | Скорость (устаревшее имя linearVelocity)
p Vector3 angularVelocity | Скорость вращения
p float mass | Масса
p bool useGravity | Действует ли гравитация
p bool isKinematic | Управляется кодом, а не физикой
p float linearDamping | Сопротивление движению
p RigidbodyConstraints constraints | Заблокированные оси
m void AddForce(Vector3 force) | Прикладывает силу
m void AddForce(Vector3 force, ForceMode mode) | Прикладывает силу заданного вида
m void AddTorque(Vector3 torque) | Прикладывает вращающую силу
m void AddExplosionForce(float explosionForce, Vector3 explosionPosition, float explosionRadius) | Сила взрыва
m void MovePosition(Vector3 position) | Двигает тело с учётом физики
m void MoveRotation(Quaternion rot) | Поворачивает тело с учётом физики
m void Sleep() | Усыпляет тело
m void WakeUp() | Будит тело
"""),
"Rigidbody2D": ("Физическое тело в 2D", """
p Vector2 linearVelocity | Скорость движения
p float angularVelocity | Скорость вращения
p float gravityScale | Множитель гравитации
p float mass | Масса
p RigidbodyType2D bodyType | Тип тела
m void AddForce(Vector2 force) | Прикладывает силу
m void AddForce(Vector2 force, ForceMode2D mode) | Прикладывает силу заданного вида
m void MovePosition(Vector2 position) | Двигает тело
m void AddTorque(float torque) | Вращающая сила
"""),
"Collider": ("Форма столкновений 3D", """
p bool isTrigger | Это триггер (без столкновений)
p bool enabled | Включён ли коллайдер
p Bounds bounds | Границы
p Rigidbody attachedRigidbody | Тело, к которому привязан
p GameObject gameObject | Объект коллайдера
p Transform transform | Transform объекта
p string tag | Тег объекта
m bool CompareTag(string tag) | Проверяет тег
m T GetComponent<T>() | Компонент T
m Vector3 ClosestPoint(Vector3 position) | Ближайшая точка коллайдера
"""),
"Collider2D": ("Форма столкновений 2D", """
p bool isTrigger | Это триггер
p GameObject gameObject | Объект коллайдера
p Transform transform | Transform объекта
m bool CompareTag(string tag) | Проверяет тег
m T GetComponent<T>() | Компонент T
"""),
"Collision": ("Данные о столкновении", """
p GameObject gameObject | Объект, с которым столкнулись
p Transform transform | Его Transform
p Collider collider | Его коллайдер
p Rigidbody rigidbody | Его тело
p Vector3 relativeVelocity | Относительная скорость
p ContactPoint[] contacts | Точки контакта
m ContactPoint GetContact(int index) | Точка контакта по номеру
"""),
"Collision2D": ("Данные о столкновении 2D", """
p GameObject gameObject | Объект, с которым столкнулись
p Transform transform | Его Transform
p Collider2D collider | Его коллайдер
p Vector2 relativeVelocity | Относительная скорость
"""),
"Input": ("Ввод с клавиатуры и мыши (старая система ввода)", """
sm float GetAxis(string axisName) | Ось ввода: "Horizontal", "Vertical", "Mouse X"…
sm float GetAxisRaw(string axisName) | Ось ввода без сглаживания
sm bool GetKey(KeyCode key) | Клавиша зажата
sm bool GetKeyDown(KeyCode key) | Клавишу только что нажали
sm bool GetKeyUp(KeyCode key) | Клавишу только что отпустили
sm bool GetButton(string buttonName) | Кнопка зажата
sm bool GetButtonDown(string buttonName) | Кнопку только что нажали
sm bool GetMouseButton(int button) | Кнопка мыши зажата
sm bool GetMouseButtonDown(int button) | Кнопку мыши только что нажали
sp Vector3 mousePosition | Позиция мыши на экране
sp bool anyKey | Зажата любая клавиша
sp int touchCount | Число касаний
sm Touch GetTouch(int index) | Касание по номеру
"""),
"Time": ("Время игры", """
sp float deltaTime | Время с прошлого кадра (сек)
sp float fixedDeltaTime | Шаг физики (сек)
sp float time | Время с начала игры
sp float timeScale | Скорость времени (0 — пауза)
sp float unscaledDeltaTime | deltaTime без учёта timeScale
sp int frameCount | Номер кадра
"""),
"Debug": ("Отладочные сообщения", """
sm void Log(object message) | Сообщение в консоль Unity
sm void LogWarning(object message) | Предупреждение в консоль
sm void LogError(object message) | Ошибка в консоль
sm void DrawLine(Vector3 start, Vector3 end, Color color) | Линия в окне сцены
sm void DrawRay(Vector3 start, Vector3 dir, Color color) | Луч в окне сцены
sm void Break() | Пауза редактора
"""),
"Mathf": ("Математические функции", """
sp float PI | Число π
sp float Infinity | Бесконечность
sp float Deg2Rad | Градусы → радианы
sp float Rad2Deg | Радианы → градусы
sm float Abs(float f) | Модуль числа
sm float Clamp(float value, float min, float max) | Ограничивает значение
sm float Clamp01(float value) | Ограничивает от 0 до 1
sm float Lerp(float a, float b, float t) | Плавный переход
sm float MoveTowards(float current, float target, float maxDelta) | Двигает к цели
sm float SmoothDamp(float current, float target, ref float currentVelocity, float smoothTime) | Плавное следование
sm float Sin(float f) | Синус
sm float Cos(float f) | Косинус
sm float Atan2(float y, float x) | Угол по координатам
sm float Sqrt(float f) | Квадратный корень
sm float Pow(float f, float p) | Степень
sm float Min(float a, float b) | Меньшее
sm float Max(float a, float b) | Большее
sm int RoundToInt(float f) | Округление до целого
sm int FloorToInt(float f) | Округление вниз
sm float PingPong(float t, float length) | Туда-обратно
sm float Sign(float f) | Знак числа
"""),
"Physics": ("Физика 3D", """
sp Vector3 gravity | Гравитация
sm bool Raycast(Vector3 origin, Vector3 direction, out RaycastHit hitInfo, float maxDistance) | Луч: во что попал
sm bool Raycast(Ray ray, out RaycastHit hitInfo) | Луч: во что попал
sm Collider[] OverlapSphere(Vector3 position, float radius) | Коллайдеры внутри сферы
sm bool SphereCast(Vector3 origin, float radius, Vector3 direction, out RaycastHit hitInfo) | Толстый луч
sm bool CheckSphere(Vector3 position, float radius) | Есть ли что-то в сфере
"""),
"Physics2D": ("Физика 2D", """
sp Vector2 gravity | Гравитация
sm RaycastHit2D Raycast(Vector2 origin, Vector2 direction, float distance) | Луч 2D
sm Collider2D OverlapCircle(Vector2 point, float radius) | Коллайдер в круге
"""),
"RaycastHit": ("Результат луча", """
p Vector3 point | Точка попадания
p Vector3 normal | Нормаль поверхности
p float distance | Расстояние
p Collider collider | Коллайдер, в который попали
p Transform transform | Его Transform
p Rigidbody rigidbody | Его тело
"""),
"Random": ("Случайные числа", """
sm float Range(float minInclusive, float maxInclusive) | Случайное дробное число
sm int Range(int minInclusive, int maxExclusive) | Случайное целое число
sp float value | Случайное число 0..1
sp Vector3 insideUnitSphere | Случайная точка в сфере
sp Vector2 insideUnitCircle | Случайная точка в круге
sp Quaternion rotation | Случайный поворот
"""),
"Application": ("Приложение", """
sm void Quit() | Выход из игры
sp bool isPlaying | Игра запущена
sp string persistentDataPath | Папка для сохранений
sp int targetFrameRate | Желаемый FPS
sp RuntimePlatform platform | Платформа
"""),
"SceneManager": ("Сцены (UnityEngine.SceneManagement)", """
sm void LoadScene(string sceneName) | Загружает сцену по имени
sm void LoadScene(int sceneBuildIndex) | Загружает сцену по номеру
sm AsyncOperation LoadSceneAsync(string sceneName) | Загружает сцену в фоне
sm Scene GetActiveScene() | Текущая сцена
"""),
"Scene": ("Сцена", """
p string name | Имя сцены
p int buildIndex | Номер в сборке
p bool isLoaded | Загружена ли
"""),
"Animator": ("Анимации", """
p float speed | Скорость анимаций
p RuntimeAnimatorController runtimeAnimatorController | Контроллер
m void SetBool(string name, bool value) | Задаёт параметр bool
m void SetFloat(string name, float value) | Задаёт параметр float
m void SetInteger(string name, int value) | Задаёт параметр int
m void SetTrigger(string name) | Запускает триггер
m void ResetTrigger(string name) | Сбрасывает триггер
m bool GetBool(string name) | Читает параметр bool
m void Play(string stateName) | Включает состояние
m void CrossFade(string stateName, float normalizedTransitionDuration) | Плавно переходит в состояние
"""),
"AudioSource": ("Источник звука", """
p AudioClip clip | Звук
p float volume | Громкость 0..1
p float pitch | Высота тона
p bool loop | Повтор
p bool isPlaying | Играет ли
m void Play() | Играть
m void Stop() | Остановить
m void Pause() | Пауза
m void PlayOneShot(AudioClip clip) | Проиграть звук один раз
m void PlayOneShot(AudioClip clip, float volumeScale) | Проиграть звук с громкостью
"""),
"Camera": ("Камера", """
sp Camera main | Главная камера сцены
p float fieldOfView | Угол обзора
p float orthographicSize | Размер ортографической камеры
p Color backgroundColor | Цвет фона
m Ray ScreenPointToRay(Vector3 pos) | Луч из точки экрана
m Vector3 ScreenToWorldPoint(Vector3 position) | Экран → мир
m Vector3 WorldToScreenPoint(Vector3 position) | Мир → экран
"""),
"Color": ("Цвет", """
p float r | Красный 0..1
p float g | Зелёный 0..1
p float b | Синий 0..1
p float a | Прозрачность 0..1
sp Color white | Белый
sp Color black | Чёрный
sp Color red | Красный
sp Color green | Зелёный
sp Color blue | Синий
sp Color yellow | Жёлтый
sp Color clear | Прозрачный
sm Color Lerp(Color a, Color b, float t) | Плавный переход цвета
"""),
"Renderer": ("Отрисовка объекта", """
p Material material | Материал
p bool enabled | Видим ли
p Bounds bounds | Границы
"""),
"Material": ("Материал", """
p Color color | Основной цвет
m void SetColor(string name, Color value) | Задаёт цвет свойства
m void SetFloat(string name, float value) | Задаёт число свойства
"""),
"SpriteRenderer": ("Отрисовка спрайта", """
p Sprite sprite | Спрайт
p Color color | Цвет
p bool flipX | Отразить по X
p bool flipY | Отразить по Y
p int sortingOrder | Порядок отрисовки
"""),
"CharacterController": ("Контроллер персонажа", """
p bool isGrounded | Стоит ли на земле
p Vector3 velocity | Скорость
m CollisionFlags Move(Vector3 motion) | Двигает с учётом столкновений
m bool SimpleMove(Vector3 speed) | Двигает с гравитацией
"""),
"PlayerPrefs": ("Простые сохранения", """
sm void SetInt(string key, int value) | Сохраняет целое
sm int GetInt(string key, int defaultValue = 0) | Читает целое
sm void SetFloat(string key, float value) | Сохраняет дробное
sm float GetFloat(string key, float defaultValue = 0f) | Читает дробное
sm void SetString(string key, string value) | Сохраняет строку
sm string GetString(string key, string defaultValue = "") | Читает строку
sm bool HasKey(string key) | Есть ли ключ
sm void DeleteKey(string key) | Удаляет ключ
sm void Save() | Записывает на диск
"""),
"Resources": ("Загрузка из папки Resources", """
sm T Load<T>(string path) | Загружает ресурс
sm T[] LoadAll<T>(string path) | Загружает все ресурсы папки
"""),
"WaitForSeconds": ("Пауза в корутине", """
"""),
"KeyCode": ("Клавиши", """
c KeyCode Space | Пробел
c KeyCode Return | Enter
c KeyCode Escape | Esc
c KeyCode LeftShift | Левый Shift
c KeyCode LeftControl | Левый Ctrl
c KeyCode Tab | Tab
c KeyCode W | W
c KeyCode A | A
c KeyCode S | S
c KeyCode D | D
c KeyCode E | E
c KeyCode R | R
c KeyCode UpArrow | Стрелка вверх
c KeyCode DownArrow | Стрелка вниз
c KeyCode LeftArrow | Стрелка влево
c KeyCode RightArrow | Стрелка вправо
c KeyCode Mouse0 | Левая кнопка мыши
c KeyCode Mouse1 | Правая кнопка мыши
c KeyCode Alpha1 | Клавиша 1
"""),
"ForceMode": ("Вид силы", """
c ForceMode Force | Постоянная сила с учётом массы
c ForceMode Impulse | Мгновенный толчок с учётом массы
c ForceMode VelocityChange | Мгновенное изменение скорости
c ForceMode Acceleration | Ускорение без учёта массы
"""),
"ForceMode2D": ("Вид силы 2D", """
c ForceMode2D Force | Постоянная сила
c ForceMode2D Impulse | Мгновенный толчок
"""),
"PrimitiveType": ("Простые фигуры", """
c PrimitiveType Cube | Куб
c PrimitiveType Sphere | Сфера
c PrimitiveType Capsule | Капсула
c PrimitiveType Cylinder | Цилиндр
c PrimitiveType Plane | Плоскость
c PrimitiveType Quad | Квадрат
"""),
"ScriptableObject": ("Ассет с данными", """
sm T CreateInstance<T>() | Создаёт экземпляр
"""),
"Coroutine": ("Запущенная корутина", ""),
"Sprite": ("Спрайт", ""),
"AudioClip": ("Звуковой файл", """
p float length | Длина в секундах
"""),
"Bounds": ("Границы", """
p Vector3 center | Центр
p Vector3 size | Размер
p Vector3 extents | Половина размера
m bool Contains(Vector3 point) | Содержит ли точку
"""),
"Ray": ("Луч", """
p Vector3 origin | Начало
p Vector3 direction | Направление
m Vector3 GetPoint(float distance) | Точка на луче
"""),
"EditorWindow": ("Окно редактора Unity (UnityEditor)", """
sm T GetWindow<T>(string title) | Открывает окно
m void Show() | Показать
m void Close() | Закрыть
m void Repaint() | Перерисовать
p string titleContent | Заголовок
"""),
"GUILayout": ("Простая разметка интерфейса (IMGUI)", """
sm bool Button(string text) | Кнопка
sm void Label(string text) | Надпись
sm string TextField(string text) | Поле ввода
sm void Space(float pixels) | Отступ
"""),
"EditorGUILayout": ("Поля редактора (UnityEditor)", """
sm float FloatField(string label, float value) | Поле числа
sm int IntField(string label, int value) | Поле целого
sm string TextField(string label, string text) | Поле текста
sm Object ObjectField(string label, Object obj, Type objType, bool allowSceneObjects) | Поле объекта
sm void LabelField(string label) | Надпись
sm void HelpBox(string message, MessageType type) | Подсказка
"""),
}

# Наследование (для подсказок по членам базовых классов)
BASES = {
    "MonoBehaviour": "Behaviour", "Behaviour": "Component", "Component": "Object",
    "Transform": "Component", "Rigidbody": "Component", "Rigidbody2D": "Component",
    "Collider": "Component", "Collider2D": "Component", "Animator": "Behaviour",
    "AudioSource": "Behaviour", "Camera": "Behaviour", "Renderer": "Component",
    "SpriteRenderer": "Renderer", "CharacterController": "Collider", "GameObject": "Object",
    "ScriptableObject": "Object", "EditorWindow": "ScriptableObject", "Material": "Object",
}

# Сообщения MonoBehaviour, которые Unity вызывает сама
MESSAGES = [
    ("Awake", "void Awake()", "Вызывается при загрузке скрипта, до Start"),
    ("Start", "void Start()", "Вызывается перед первым кадром"),
    ("Update", "void Update()", "Вызывается каждый кадр"),
    ("FixedUpdate", "void FixedUpdate()", "Вызывается с шагом физики — для Rigidbody"),
    ("LateUpdate", "void LateUpdate()", "Каждый кадр после всех Update — для камеры"),
    ("OnEnable", "void OnEnable()", "Когда объект или компонент включается"),
    ("OnDisable", "void OnDisable()", "Когда объект или компонент выключается"),
    ("OnDestroy", "void OnDestroy()", "Перед удалением объекта"),
    ("OnCollisionEnter", "void OnCollisionEnter(Collision collision)", "Начало столкновения"),
    ("OnCollisionExit", "void OnCollisionExit(Collision collision)", "Конец столкновения"),
    ("OnTriggerEnter", "void OnTriggerEnter(Collider other)", "Вход в триггер"),
    ("OnTriggerStay", "void OnTriggerStay(Collider other)", "Нахождение в триггере"),
    ("OnTriggerExit", "void OnTriggerExit(Collider other)", "Выход из триггера"),
    ("OnCollisionEnter2D", "void OnCollisionEnter2D(Collision2D collision)", "Начало столкновения 2D"),
    ("OnTriggerEnter2D", "void OnTriggerEnter2D(Collider2D other)", "Вход в триггер 2D"),
    ("OnTriggerExit2D", "void OnTriggerExit2D(Collider2D other)", "Выход из триггера 2D"),
    ("OnMouseDown", "void OnMouseDown()", "Клик мышью по коллайдеру объекта"),
    ("OnBecameVisible", "void OnBecameVisible()", "Объект стал виден камере"),
    ("OnValidate", "void OnValidate()", "Значение изменили в инспекторе"),
    ("OnDrawGizmos", "void OnDrawGizmos()", "Рисует подсказки в окне сцены"),
    ("OnGUI", "void OnGUI()", "Рисует простой интерфейс (IMGUI)"),
    ("OnApplicationQuit", "void OnApplicationQuit()", "Перед выходом из игры"),
]

ATTRIBUTES = [
    ("SerializeField", "Показать приватное поле в инспекторе"),
    ("Header", "Заголовок над полем в инспекторе: [Header(\"Текст\")]"),
    ("Range", "Ползунок в инспекторе: [Range(0, 10)]"),
    ("Tooltip", "Подсказка в инспекторе"),
    ("HideInInspector", "Скрыть публичное поле"),
    ("RequireComponent", "Обязательный компонент: [RequireComponent(typeof(Rigidbody))]"),
    ("CreateAssetMenu", "Пункт меню для ScriptableObject"),
    ("MenuItem", "Пункт меню редактора (UnityEditor)"),
    ("System.Serializable", "Класс можно показать в инспекторе"),
]

CS_KEYWORDS = ("public private protected internal static readonly const void class struct "
               "interface enum namespace using new this base override virtual abstract "
               "sealed partial null true false var in out ref is as get set return if else "
               "for foreach while do switch case default break continue try catch finally "
               "throw yield async await event delegate typeof nameof int float double bool "
               "string char byte long object").split()

PRIMITIVES = {"int", "float", "double", "bool", "string", "char", "byte", "long", "object", "void"}

CLASSES = {}


class Member:
    __slots__ = ("name", "kind", "sig", "doc", "static", "type", "owner")

    def __init__(self, name, kind, sig, doc, static, type_, owner):
        self.name, self.kind, self.sig, self.doc = name, kind, sig, doc
        self.static, self.type, self.owner = static, type_, owner


def _parse():
    for cls, (doc, body) in _SPEC.items():
        members = []
        for line in body.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            head, _, mdoc = line.partition("|")
            k, _, sig = head.strip().partition(" ")
            sig = sig.strip()
            if "(" in sig:
                m = re.search(r"(\w+)\s*(?:<[^>]*>)?\s*\(", sig)
                name = m.group(1)
                kind = "method"
            else:
                name = sig.split()[-1]
                kind = "const" if k == "c" else "property"
            type_ = sig.split()[0]
            members.append(Member(name, kind, sig, mdoc.strip(), k in ("sm", "sp", "c"), type_, cls))
        CLASSES[cls] = {"doc": doc, "members": members}


_parse()


def members_of(cls, static=None):
    """Члены класса вместе с базовыми классами."""
    out, seen = [], set()
    c = cls
    while c:
        for m in CLASSES.get(c, {}).get("members", []):
            if static is not None and m.static != static:
                continue
            key = (m.name, m.sig)
            if key not in seen:
                seen.add(key)
                out.append(m)
        c = BASES.get(c)
    return out


def find_members(cls, name):
    return [m for m in members_of(cls) if m.name == name]
