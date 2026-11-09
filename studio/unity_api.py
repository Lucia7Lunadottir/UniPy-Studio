"""Справочник Unity API для подсказок C#.

Строка члена: «вид сигнатура», где вид:
  m — метод экземпляра, p — свойство экземпляра,
  sm — статический метод, sp — статическое свойство, c — значение перечисления.
Описания — в locales/<язык>.json: unity.<Класс> и unity.<Класс>.<член> (перегрузки: _2, _3…).
"""
import re

_SPEC = {
"Object": """
p string name
m int GetInstanceID()
sm Object Instantiate(Object original)
sm Object Instantiate(Object original, Vector3 position, Quaternion rotation)
sm Object Instantiate(Object original, Transform parent)
sm void Destroy(Object obj, float t = 0.0f)
sm void DontDestroyOnLoad(Object target)
sm T FindFirstObjectByType<T>()
sm T[] FindObjectsByType<T>(FindObjectsSortMode sortMode)
""",
"Component": """
p Transform transform
p GameObject gameObject
p string tag
m T GetComponent<T>()
m T[] GetComponents<T>()
m T GetComponentInChildren<T>()
m T GetComponentInParent<T>()
m bool TryGetComponent<T>(out T component)
m bool CompareTag(string tag)
m void SendMessage(string methodName)
""",
"Behaviour": """
p bool enabled
p bool isActiveAndEnabled
""",
"MonoBehaviour": """
m Coroutine StartCoroutine(IEnumerator routine)
m void StopCoroutine(Coroutine routine)
m void StopAllCoroutines()
m void Invoke(string methodName, float time)
m void InvokeRepeating(string methodName, float time, float repeatRate)
m void CancelInvoke()
m bool IsInvoking(string methodName)
sm void print(object message)
""",
"GameObject": """
p Transform transform
p bool activeSelf
p bool activeInHierarchy
p string tag
p int layer
p string name
p Scene scene
m void SetActive(bool value)
m T GetComponent<T>()
m T AddComponent<T>()
m bool TryGetComponent<T>(out T component)
m T GetComponentInChildren<T>()
m bool CompareTag(string tag)
sm GameObject Find(string name)
sm GameObject FindWithTag(string tag)
sm GameObject[] FindGameObjectsWithTag(string tag)
sm GameObject CreatePrimitive(PrimitiveType type)
""",
"Transform": """
p Vector3 position
p Vector3 localPosition
p Quaternion rotation
p Quaternion localRotation
p Vector3 eulerAngles
p Vector3 localEulerAngles
p Vector3 localScale
p Vector3 forward
p Vector3 right
p Vector3 up
p Transform parent
p int childCount
p GameObject gameObject
m void Translate(Vector3 translation)
m void Translate(float x, float y, float z)
m void Rotate(Vector3 eulers)
m void Rotate(float xAngle, float yAngle, float zAngle)
m void RotateAround(Vector3 point, Vector3 axis, float angle)
m void LookAt(Transform target)
m void LookAt(Vector3 worldPosition)
m void SetParent(Transform parent)
m void SetPositionAndRotation(Vector3 position, Quaternion rotation)
m Transform GetChild(int index)
m Transform Find(string name)
m Vector3 TransformPoint(Vector3 position)
m Vector3 InverseTransformPoint(Vector3 position)
""",
"Vector3": """
p float x
p float y
p float z
p float magnitude
p float sqrMagnitude
p Vector3 normalized
m void Normalize()
m void Set(float x, float y, float z)
sp Vector3 zero
sp Vector3 one
sp Vector3 up
sp Vector3 down
sp Vector3 forward
sp Vector3 back
sp Vector3 left
sp Vector3 right
sm float Distance(Vector3 a, Vector3 b)
sm Vector3 Lerp(Vector3 a, Vector3 b, float t)
sm Vector3 MoveTowards(Vector3 current, Vector3 target, float maxDistanceDelta)
sm float Dot(Vector3 lhs, Vector3 rhs)
sm Vector3 Cross(Vector3 lhs, Vector3 rhs)
sm float Angle(Vector3 from, Vector3 to)
sm Vector3 ClampMagnitude(Vector3 vector, float maxLength)
sm Vector3 Reflect(Vector3 inDirection, Vector3 inNormal)
sm Vector3 SmoothDamp(Vector3 current, Vector3 target, ref Vector3 currentVelocity, float smoothTime)
""",
"Vector2": """
p float x
p float y
p float magnitude
p Vector2 normalized
sp Vector2 zero
sp Vector2 one
sp Vector2 up
sp Vector2 down
sp Vector2 left
sp Vector2 right
sm float Distance(Vector2 a, Vector2 b)
sm Vector2 Lerp(Vector2 a, Vector2 b, float t)
sm Vector2 MoveTowards(Vector2 current, Vector2 target, float maxDistanceDelta)
""",
"Quaternion": """
p Vector3 eulerAngles
sp Quaternion identity
sm Quaternion Euler(float x, float y, float z)
sm Quaternion Euler(Vector3 euler)
sm Quaternion LookRotation(Vector3 forward)
sm Quaternion Lerp(Quaternion a, Quaternion b, float t)
sm Quaternion Slerp(Quaternion a, Quaternion b, float t)
sm Quaternion RotateTowards(Quaternion from, Quaternion to, float maxDegreesDelta)
sm Quaternion AngleAxis(float angle, Vector3 axis)
sm float Angle(Quaternion a, Quaternion b)
""",
"Rigidbody": """
p Vector3 linearVelocity
p Vector3 velocity
p Vector3 angularVelocity
p float mass
p bool useGravity
p bool isKinematic
p float linearDamping
p RigidbodyConstraints constraints
m void AddForce(Vector3 force)
m void AddForce(Vector3 force, ForceMode mode)
m void AddTorque(Vector3 torque)
m void AddExplosionForce(float explosionForce, Vector3 explosionPosition, float explosionRadius)
m void MovePosition(Vector3 position)
m void MoveRotation(Quaternion rot)
m void Sleep()
m void WakeUp()
""",
"Rigidbody2D": """
p Vector2 linearVelocity
p float angularVelocity
p float gravityScale
p float mass
p RigidbodyType2D bodyType
m void AddForce(Vector2 force)
m void AddForce(Vector2 force, ForceMode2D mode)
m void MovePosition(Vector2 position)
m void AddTorque(float torque)
""",
"Collider": """
p bool isTrigger
p bool enabled
p Bounds bounds
p Rigidbody attachedRigidbody
p GameObject gameObject
p Transform transform
p string tag
m bool CompareTag(string tag)
m T GetComponent<T>()
m Vector3 ClosestPoint(Vector3 position)
""",
"Collider2D": """
p bool isTrigger
p GameObject gameObject
p Transform transform
m bool CompareTag(string tag)
m T GetComponent<T>()
""",
"Collision": """
p GameObject gameObject
p Transform transform
p Collider collider
p Rigidbody rigidbody
p Vector3 relativeVelocity
p ContactPoint[] contacts
m ContactPoint GetContact(int index)
""",
"Collision2D": """
p GameObject gameObject
p Transform transform
p Collider2D collider
p Vector2 relativeVelocity
""",
"Input": """
sm float GetAxis(string axisName)
sm float GetAxisRaw(string axisName)
sm bool GetKey(KeyCode key)
sm bool GetKeyDown(KeyCode key)
sm bool GetKeyUp(KeyCode key)
sm bool GetButton(string buttonName)
sm bool GetButtonDown(string buttonName)
sm bool GetMouseButton(int button)
sm bool GetMouseButtonDown(int button)
sp Vector3 mousePosition
sp bool anyKey
sp int touchCount
sm Touch GetTouch(int index)
""",
"Time": """
sp float deltaTime
sp float fixedDeltaTime
sp float time
sp float timeScale
sp float unscaledDeltaTime
sp int frameCount
""",
"Debug": """
sm void Log(object message)
sm void LogWarning(object message)
sm void LogError(object message)
sm void DrawLine(Vector3 start, Vector3 end, Color color)
sm void DrawRay(Vector3 start, Vector3 dir, Color color)
sm void Break()
""",
"Mathf": """
sp float PI
sp float Infinity
sp float Deg2Rad
sp float Rad2Deg
sm float Abs(float f)
sm float Clamp(float value, float min, float max)
sm float Clamp01(float value)
sm float Lerp(float a, float b, float t)
sm float MoveTowards(float current, float target, float maxDelta)
sm float SmoothDamp(float current, float target, ref float currentVelocity, float smoothTime)
sm float Sin(float f)
sm float Cos(float f)
sm float Atan2(float y, float x)
sm float Sqrt(float f)
sm float Pow(float f, float p)
sm float Min(float a, float b)
sm float Max(float a, float b)
sm int RoundToInt(float f)
sm int FloorToInt(float f)
sm float PingPong(float t, float length)
sm float Sign(float f)
""",
"Physics": """
sp Vector3 gravity
sm bool Raycast(Vector3 origin, Vector3 direction, out RaycastHit hitInfo, float maxDistance)
sm bool Raycast(Ray ray, out RaycastHit hitInfo)
sm Collider[] OverlapSphere(Vector3 position, float radius)
sm bool SphereCast(Vector3 origin, float radius, Vector3 direction, out RaycastHit hitInfo)
sm bool CheckSphere(Vector3 position, float radius)
""",
"Physics2D": """
sp Vector2 gravity
sm RaycastHit2D Raycast(Vector2 origin, Vector2 direction, float distance)
sm Collider2D OverlapCircle(Vector2 point, float radius)
""",
"RaycastHit": """
p Vector3 point
p Vector3 normal
p float distance
p Collider collider
p Transform transform
p Rigidbody rigidbody
""",
"Random": """
sm float Range(float minInclusive, float maxInclusive)
sm int Range(int minInclusive, int maxExclusive)
sp float value
sp Vector3 insideUnitSphere
sp Vector2 insideUnitCircle
sp Quaternion rotation
""",
"Application": """
sm void Quit()
sp bool isPlaying
sp string persistentDataPath
sp int targetFrameRate
sp RuntimePlatform platform
""",
"SceneManager": """
sm void LoadScene(string sceneName)
sm void LoadScene(int sceneBuildIndex)
sm AsyncOperation LoadSceneAsync(string sceneName)
sm Scene GetActiveScene()
""",
"Scene": """
p string name
p int buildIndex
p bool isLoaded
""",
"Animator": """
p float speed
p RuntimeAnimatorController runtimeAnimatorController
m void SetBool(string name, bool value)
m void SetFloat(string name, float value)
m void SetInteger(string name, int value)
m void SetTrigger(string name)
m void ResetTrigger(string name)
m bool GetBool(string name)
m void Play(string stateName)
m void CrossFade(string stateName, float normalizedTransitionDuration)
""",
"AudioSource": """
p AudioClip clip
p float volume
p float pitch
p bool loop
p bool isPlaying
m void Play()
m void Stop()
m void Pause()
m void PlayOneShot(AudioClip clip)
m void PlayOneShot(AudioClip clip, float volumeScale)
""",
"Camera": """
sp Camera main
p float fieldOfView
p float orthographicSize
p Color backgroundColor
m Ray ScreenPointToRay(Vector3 pos)
m Vector3 ScreenToWorldPoint(Vector3 position)
m Vector3 WorldToScreenPoint(Vector3 position)
""",
"Color": """
p float r
p float g
p float b
p float a
sp Color white
sp Color black
sp Color red
sp Color green
sp Color blue
sp Color yellow
sp Color clear
sm Color Lerp(Color a, Color b, float t)
""",
"Renderer": """
p Material material
p bool enabled
p Bounds bounds
""",
"Material": """
p Color color
m void SetColor(string name, Color value)
m void SetFloat(string name, float value)
""",
"SpriteRenderer": """
p Sprite sprite
p Color color
p bool flipX
p bool flipY
p int sortingOrder
""",
"CharacterController": """
p bool isGrounded
p Vector3 velocity
m CollisionFlags Move(Vector3 motion)
m bool SimpleMove(Vector3 speed)
""",
"PlayerPrefs": """
sm void SetInt(string key, int value)
sm int GetInt(string key, int defaultValue = 0)
sm void SetFloat(string key, float value)
sm float GetFloat(string key, float defaultValue = 0f)
sm void SetString(string key, string value)
sm string GetString(string key, string defaultValue = "")
sm bool HasKey(string key)
sm void DeleteKey(string key)
sm void Save()
""",
"Resources": """
sm T Load<T>(string path)
sm T[] LoadAll<T>(string path)
""",
"WaitForSeconds": """
""",
"KeyCode": """
c KeyCode Space
c KeyCode Return
c KeyCode Escape
c KeyCode LeftShift
c KeyCode LeftControl
c KeyCode Tab
c KeyCode W
c KeyCode A
c KeyCode S
c KeyCode D
c KeyCode E
c KeyCode R
c KeyCode UpArrow
c KeyCode DownArrow
c KeyCode LeftArrow
c KeyCode RightArrow
c KeyCode Mouse0
c KeyCode Mouse1
c KeyCode Alpha1
""",
"ForceMode": """
c ForceMode Force
c ForceMode Impulse
c ForceMode VelocityChange
c ForceMode Acceleration
""",
"ForceMode2D": """
c ForceMode2D Force
c ForceMode2D Impulse
""",
"PrimitiveType": """
c PrimitiveType Cube
c PrimitiveType Sphere
c PrimitiveType Capsule
c PrimitiveType Cylinder
c PrimitiveType Plane
c PrimitiveType Quad
""",
"ScriptableObject": """
sm T CreateInstance<T>()
""",
"Coroutine": """""",
"Sprite": """""",
"AudioClip": """
p float length
""",
"Bounds": """
p Vector3 center
p Vector3 size
p Vector3 extents
m bool Contains(Vector3 point)
""",
"Ray": """
p Vector3 origin
p Vector3 direction
m Vector3 GetPoint(float distance)
""",
"EditorWindow": """
sm T GetWindow<T>(string title)
m void Show()
m void Close()
m void Repaint()
p string titleContent
""",
"GUILayout": """
sm bool Button(string text)
sm void Label(string text)
sm string TextField(string text)
sm void Space(float pixels)
""",
"EditorGUILayout": """
sm float FloatField(string label, float value)
sm int IntField(string label, int value)
sm string TextField(string label, string text)
sm Object ObjectField(string label, Object obj, Type objType, bool allowSceneObjects)
sm void LabelField(string label)
sm void HelpBox(string message, MessageType type)
""",
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
    ("Awake", "void Awake()", "unity_api.called_when_script_loaded_before"),
    ("Start", "void Start()", "unity_api.called_before_first_frame"),
    ("Update", "void Update()", "unity_api.update"),
    ("FixedUpdate", "void FixedUpdate()", "unity_api.called_with_physics_step_rigidbody"),
    ("LateUpdate", "void LateUpdate()", "unity_api.late_update"),
    ("OnEnable", "void OnEnable()", "unity_api.when_object_component_switched"),
    ("OnDisable", "void OnDisable()", "unity_api.when_object_component_switched_off"),
    ("OnDestroy", "void OnDestroy()", "unity_api.before_removing_object"),
    ("OnCollisionEnter", "void OnCollisionEnter(Collision collision)", "unity_api.start_collision"),
    ("OnCollisionExit", "void OnCollisionExit(Collision collision)", "unity_api.end_collision"),
    ("OnTriggerEnter", "void OnTriggerEnter(Collider other)", "unity_api.trigger_entry"),
    ("OnTriggerStay", "void OnTriggerStay(Collider other)", "unity_api.on_trigger_stay"),
    ("OnTriggerExit", "void OnTriggerExit(Collider other)", "unity_api.exit_from_trigger"),
    ("OnCollisionEnter2D", "void OnCollisionEnter2D(Collision2D collision)", "unity_api.start_2d_collision"),
    ("OnTriggerEnter2D", "void OnTriggerEnter2D(Collider2D other)", "unity_api.2d_trigger_entry"),
    ("OnTriggerExit2D", "void OnTriggerExit2D(Collider2D other)", "unity_api.exit_from_2d_trigger"),
    ("OnMouseDown", "void OnMouseDown()", "unity_api.on_mouse_down"),
    ("OnBecameVisible", "void OnBecameVisible()", "unity_api.object_visible_camera"),
    ("OnValidate", "void OnValidate()", "unity_api.changed_value_inspector"),
    ("OnDrawGizmos", "void OnDrawGizmos()", "unity_api.on_draw_gizmos"),
    ("OnGUI", "void OnGUI()", "unity_api.drawing_simple_interface_imgui"),
    ("OnApplicationQuit", "void OnApplicationQuit()", "unity_api.before_you_leave_game"),
]

ATTRIBUTES = [
    ("SerializeField", "unity_api.show_private_field_inspector"),
    ("Header", "unity_api.headline_over_field_inspector_header"),
    ("Range", "unity_api.attr_range"),
    ("Tooltip", "unity_api.inspector_tooltip"),
    ("HideInInspector", "unity_api.hide_public_field"),
    ("RequireComponent", "unity_api.required_component_requirecomponent_type_rigid"),
    ("CreateAssetMenu", "unity_api.menu_item_scriptableobject"),
    ("MenuItem", "unity_api.unityeditor_menu_item"),
    ("System.Serializable", "unity_api.class_can_shown_inspector"),
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
    for cls, body in _SPEC.items():
        members = []
        overloads = {}
        for line in body.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            k, _, sig = line.partition(" ")
            sig = sig.strip()
            if "(" in sig:
                m = re.search(r"(\w+)\s*(?:<[^>]*>)?\s*\(", sig)
                name = m.group(1)
                kind = "method"
            else:
                name = sig.split()[-1]
                kind = "const" if k == "c" else "property"
            type_ = sig.split()[0]
            overloads[name] = overloads.get(name, 0) + 1
            doc = "unity.%s.%s" % (cls, name) + ("_%d" % overloads[name] if overloads[name] > 1 else "")
            members.append(Member(name, kind, sig, doc, k in ("sm", "sp", "c"), type_, cls))
        CLASSES[cls] = {"doc": "unity." + cls, "members": members}


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
