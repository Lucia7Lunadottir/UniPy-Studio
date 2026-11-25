# Чего UniPy Studio не хватает для Unity по сравнению с Rider

Составлено 26.11.2026.

**Источники:**
- список проверок из установленного Rider (`plugins/rider-unity/dotnet/JetBrains.ReSharper.Plugins.Unity.dll`).
  Это плагин resharper-unity, его исходники открыты (JetBrains, Apache 2.0);
- правила Microsoft.Unity.Analyzers (MIT): 46 правил UNT, 23 подавителя USP;
- замеры UniPy на The Mirror (Unity 6000.6.4f1, 187 скриптов).

## Замер: что UniPy показывает сейчас

The Mirror, все скрипты Assets, тот же Roslyn и те же настройки, что в IDE:

- красных ошибок и жёлтых предупреждений — **0**. ⚠ Первый вывод «ложных красных нет» был **неверным**:
  0 означал, что Roslyn вообще не показывал ошибки компилятора (даже на `int x = "text";`). Причины и
  исправление — в разделе «Главная найденная ошибка» ниже;
- в подсказках (лампочка, бледный код):

| Правило | Сколько | Правильно ли для Unity |
|---|---|---|
| IDE0044 «сделать поле readonly» | 64 | **нет** почти везде: это поля `[SerializeField]`, после `readonly` Unity перестаёт их сериализовать |
| IDE0060 «удалить неиспользуемый параметр» | 17 | частично: у сообщений Unity (`OnTriggerEnter(Collider other)`) параметр удалять нельзя |
| IDE0031 «упростить проверку null» (`?.`) | 2 | **нет**: `?.` на объекте Unity не видит уничтоженный объект, это ошибка логики |
| IDE0005 лишние `using` | 186 | да |

### Главная найденная ошибка: ошибки компилятора не показывались вовсе (исправлено)
Roslyn, не найдя DLL из `.csproj`, пишет «имеются неразрешённые зависимости» и **молча отключает ошибки
компилятора**. Причин было две:
1. **наша**: копия решения в `~/.cache/unipy-studio/roslyn` сохраняла относительные `<HintPath>`
   (`Library/PackageCache/…`, `Assets/Plugins/…`) — из кэша они вели в никуда. Сломанных ссылок:
   World without Time 2188, WWT-Mechanics 2357, narfi-s-adventures 804. Теперь пути в копии абсолютные;
2. **после обновления Unity** `.csproj` ссылаются на удалённую версию (The Mirror: 6000.6.4f1, все 367 ссылок)
   и на пакеты со старым хэшем. Теперь `studio/unity_references.py` подставляет версию из `ProjectVersion.txt`
   и текущую папку пакета, а то, что найти нельзя, IDE показывает предупреждением в строке состояния и «Выводе».

Проверка: в The Mirror `int bad = "text"; NoSuchThing();` → CS0029 и CS0103 (раньше — ничего).

**Вывод:** «базовые косяки» — это не ошибки самого Roslyn. Ему не хватает **знаний об Unity**:
в Visual Studio и VS Code их даёт Microsoft.Unity.Analyzers, в Rider — плагин resharper-unity.

### Проверка Microsoft.Unity.Analyzers в UniPy

Анализатор подключён к копии решения (`unity_light_solution.build(..., analyzers=[...])`):

- работает: UNT0001 «пустой Update / OnTriggerEnter», подавлен IDE0060 у `OnTriggerEnter(Collider other)`,
  подавлен IDE0044 у `[UnityEngine.SerializeField] private int _hp;`;
- **не работает**: IDE0044 у `[SerializeField] private bool _a;` (короткое имя через `using UnityEngine;`)
  **не подавляется**. А так пишут почти все поля в проектах. Подавитель (USP0004, `SerializedFieldSuppressor`)
  ищет поле по месту диагностики и сверяет атрибут по типу. Причина расхождения пока не найдена;
  это первое, что разбирать в пункте 1.
- проверка шла на DLL из установленного расширения VS Code (vstuc 1.3.2). Свежую 1.28.0 с NuGet
  запустить не удалось: загрузку скачанной DLL в процесс блокирует автоматический режим Claude Code.

---

## Список недостающего (по важности для «не косячить в базовом»)

### 1. Правила Unity в анализаторе — Microsoft.Unity.Analyzers ⟵ начинать с этого
Что даёт (как в Visual Studio):
- **подавляет ложные** IDE0044 (readonly у SerializeField), IDE0051/IDE0052 (Update «не используется»),
  IDE0060 (параметры сообщений), CS0649 («поле никогда не присваивается» у SerializeField),
  CS8618, CA1822 («сделать static» у сообщений), IDE1006 (имена);
- **ловит ошибки Unity**: `?.` / `??` / `is null` на объектах Unity (UNT0007, UNT0008, UNT0029),
  неверная сигнатура сообщения (UNT0006), опечатка в регистре `update()` (UNT0033), `new MonoBehaviour()` (UNT0010),
  `new ScriptableObject()` (UNT0011), `tag ==` вместо `CompareTag` (UNT0002), `Time.deltaTime` в FixedUpdate (UNT0005),
  `GetComponent` → `TryGetComponent` (UNT0026), пустые сообщения (UNT0001) и т.д.

Как подключить:
- (а) **IDE**: копия решения дописывает `<Analyzer Include=…/>` во все проекты, если Unity его не записал.
  Механизм уже готов: `unity_light_solution.build(root, sln, analyzers=…)` с тестом. Осталось научить
  `RoslynServer` передавать путь к DLL.
- (б) **откуда DLL**: NuGet `Microsoft.Unity.Analyzers` (Microsoft, MIT). Скачивать кнопкой в
  «Справка → Обновления анализаторов…» с самопроверкой и откатом, как roslyn-language-server.
  Либо положить DLL в пакет `com.unipy.ide` (MIT позволяет, с файлом лицензии), тогда `UniPyProjectPostprocessor`
  впишет его в `.csproj` и в самом Unity.
- (в) после подключения убрать самодельный фильтр `RoslynServer.keep_faded` (IDE0051 по именам сообщений):
  эту работу делает подавитель.
- **решить до этого**: почему не подавляется `[SerializeField]` с коротким именем (см. замер выше).

Закрытое расширение `UnityCodeAnalysis.dll`, которое VS Code-расширение Unity грузит в Roslyn
(`csharpExtensionLoadPaths`), **брать нельзя**: лицензия только для VS Code.

### 2. Ошибки по всему решению, а не только по открытым файлам
Rider: «Solution-Wide Analysis». Visual Studio: список ошибок после сборки + «Entire solution».
У нас: `dotnet_*_diagnostics_scope = openFiles`. Переименовала метод — вызовы в закрытых файлах красными
не станут, пока Unity не скомпилирует. Вариант: параметр «Анализ: открытые файлы / всё решение»
(Roslyn `fullSolution` + запрос `workspace/diagnostic`), на большом проекте — только в простое.

### 3. Переименование с учётом Unity
- переименование класса MonoBehaviour → переименовать файл **и `.meta`** (иначе Unity теряет связь со сценами);
- переименование `[SerializeField]` поля → предложить `[FormerlySerializedAs("старое")]`, иначе значения
  в инспекторе пропадут (Rider: `RedundantFormerlySerializedAsAttribute` и автодобавление);
- перемещение/удаление `.cs` в обозревателе → вместе с `.meta`.

### 4. Проверка строк по данным проекта (только Rider)
Rider читает ProjectSettings и проверяет строковые литералы:
`UnknownTag` (`CompareTag("Playr")`), `UnknownLayer` (`LayerMask.NameToLayer`), `LoadScene*` (сцена не в Build Settings,
неверный индекс), `UnknownInputAxes` (`Input.GetAxis`), `UnknownAnimatorStateName`, `UnknownResource` (`Resources.Load`),
`PreferAddressByIdToGraphicsParams` (`Shader.PropertyToID`). Плюс **подсказки** этих строк при наборе.
Источник данных: `ProjectSettings/TagManager.asset`, `EditorBuildSettings.asset`, `InputManager.asset` (YAML) —
можно читать без Unity.

### 5. Использование в ассетах
Rider индексирует сцены/префабы (YAML) и показывает:
- метод, вызываемый из `UnityEvent` (кнопка OnClick) — **не «неиспользуемый»**, «Найти ссылки» находит сцену;
- значения `[SerializeField]` из префабов прямо в коде (Code Vision);
- сколько объектов используют скрипт.

### 6. Производительность (только Rider)
Подсветка «дорогих» вызовов в Update и всём, что из него вызывается: `GetComponent`, `Find`, `Camera.main`,
сравнение с null, `AddComponent` (`Unity.ExpensiveCode`, `PerformanceCritical*`), `InefficientMultiplicationOrder`,
`PreferNonAllocApi`, `InstantiateWithoutParent`. Microsoft.Unity.Analyzers покрывает часть (UNT0024, UNT0028 и др.).

### 7. Сообщения Unity
- создание `Update`/`OnCollisionEnter` с **правильной сигнатурой** из списка (Alt+Insert в Rider). У нас подсказка
  имени есть (`unity_api.MESSAGES`), правильность сигнатуры проверит UNT0006 после пункта 1;
- значок на полях слева «это сообщение Unity / сериализуемое поле».

### 8. Файлы Unity
- `.asmdef` / `.asmref`: подсказки имён сборок, проверка ссылок, `versionDefines`
  (Rider: `MismatchedAsmDefFilename`, `InvalidVersionDefine*`);
- ShaderLab + HLSL (`.shader`, `.hlsl`): у нас только подсветка;
- Burst (17 проверок `Burst*`) и DOTS-генерация — если будут проекты на ECS.

### 9. Связь с запущенным Unity
Rider через пакет `com.unity.ide.rider` держит канал с Unity:
Play / Pause / Step из IDE, «Refresh» ассетов при сохранении, запуск тестов Unity Test Runner, отладчик
(Mono soft debugger), просмотр текстур в отладчике. У нас: консоль Unity из `Logs/Editor.log`, открытие
файлов из Unity. Отладчик Unity отложен (решение 11-09).

---

## Что уже есть и не хуже Rider
Скорость подсказок (0,04–0,12 с на World without Time), память (IDE ~200 МБ + Roslyn ~700 МБ против 3,2 ГБ у Rider),
лёгкое решение с готовыми DLL пакетов, новые скрипты без перегенерации, F12 в декомпилированный UnityEngine,
переименование, форматирование, сборка, консоль Unity.

## Предлагаемый порядок
1 → 3 → 2 → 4 → 5 → 7 → 6 → 8 → 9. Пункты 1 и 3 закрывают «косячим в базовом»: неправильные советы
и потерю данных в Unity. Остальное — удобство, которое есть только в Rider.
