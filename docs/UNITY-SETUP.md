# UniPy Studio + Unity: что установить и откуда

Документ о том, из чего складывается работа с Unity: какие пакеты нужны, откуда их брать,
что проверить, когда подсказки или ошибки ведут себя странно.

Все внешние части — **официальные пакеты Microsoft или Unity**. Сторонних сборок нет.

---

## 1. Как это устроено (коротко)

```
Unity Editor ──(пакеты com.unity.ide.visualstudio + com.unipy.ide)──▶ .sln / .csproj в корне проекта
                                                                     │
UniPy Studio ──▶ копия решения в ~/.cache/unipy-studio/roslyn/<хэш>   │  (unity_light_solution.py:
                 (пакеты Unity → готовые DLL, новые скрипты дописаны) ◀┘   проект Unity не меняется)
                 │
                 ▼
     roslyn-language-server (Microsoft, движок IntelliSense Visual Studio)
                 │
                 ▼
     подсказки, ошибки, F12, переименование, лампочка
```

Подсказки и ошибки ровно настолько правильные, насколько правильны **.csproj от Unity**.
Если Unity давно не перегенерировал проекты, Roslyn видит старый набор файлов и ссылок.

---

## 2. Обязательные пакеты (Linux, Arch)

| Что | Зачем | Откуда | Команда |
|---|---|---|---|
| Python 3, PyQt6, jedi, pygments, pyte | сама IDE | официальные репозитории Arch (`extra`) | `sudo pacman -S python-pyqt6 python-jedi python-pygments python-pyte` |
| **.NET SDK 10** | на нём работает анализатор C# и `dotnet build` | Arch `extra/dotnet-sdk` (собран из исходников Microsoft) | `sudo pacman -S dotnet-sdk` |
| **roslyn-language-server** | движок C#: подсказки, ошибки, переходы | NuGet, пакет Microsoft (репозиторий dotnet/roslyn, MIT) | `dotnet tool install --global roslyn-language-server --prerelease` |
| git | Unity скачивает пакет `com.unipy.ide` по git-ссылке; панель Git в IDE | `extra/git` | `sudo pacman -S git` |

Проверка:

```sh
dotnet --list-sdks                          # должна быть строка 10.x
~/.dotnet/tools/roslyn-language-server --version
```

Обновлять анализатор — **только через «Справка → Обновления анализаторов…»** (самопроверка
и автоматический откат). Команда `dotnet tool update` в терминале проверки и отката не делает.

## 3. Пакеты внутри Unity-проекта

| Пакет | Откуда | Зачем |
|---|---|---|
| `com.unity.ide.visualstudio` (Visual Studio Editor) ≥ 2.0.20 | Unity Registry (Package Manager) | генератор `.sln` / `.csproj`. Сама Visual Studio не нужна |
| `com.unipy.ide` | `https://github.com/Lucia7Lunadottir/UniPy-Studio.git?path=/unity/com.unipy.ide` | регистрирует UniPy в Preferences → External Tools, открывает файл на нужной строке, перегенерирует проекты, когда меняются пакеты |
| `com.unity.ide.rider` | Unity Registry | **не обязателен**. Не мешает, но если выбран внешним редактором Rider, проекты пишет он |

После установки: **Edit → Preferences → External Tools → External Script Editor = UniPy Studio**
и кнопка **Regenerate project files** там же.

### Когда нажимать «Regenerate project files»

- добавили или удалили пакет / `.asmdef` / `.asmref`;
- поменяли Scripting Define Symbols, версию API или платформу сборки;
- в UniPy у файла серые `using` и нет ни ошибок, ни классов Unity (файл вне проекта).
  Новые скрипты UniPy сам дописывает в копию проекта, но новый `.asmdef` без перегенерации
  не появится.

## 4. Необязательно, но полезно

| Что | Зачем | Откуда |
|---|---|---|
| **Microsoft.Unity.Analyzers** | правила Unity в анализаторе (см. §5) | NuGet, Microsoft, MIT: https://www.nuget.org/packages/Microsoft.Unity.Analyzers (на 26.11.2026 — 1.28.0). **Пока не подключён в UniPy автоматически**, см. docs/RIDER-GAP.md |
| `extra/python-debugpy` | отладчик Python | `sudo pacman -S python-debugpy` |
| `extra/ruff` или `extra/python-black` | форматирование Python | `sudo pacman -S ruff` |
| `extra/nodejs` + `npm` → pyright | ошибки Python | `npm install -g --prefix ~/.local pyright` |

**Не нужны:** Mono, Visual Studio, VS Code, Rider. Сообщение анализатора в журнале
«An installation of Mono MSBuild could not be found; … will be loaded with the .NET Core SDK» —
**нормальное**, проекты Unity грузятся через .NET SDK.

## 5. Почему «ошибки неверные»: известные причины

| Симптом | Причина | Что делать |
|---|---|---|
| Лампочка предлагает «сделать поле readonly» для `[SerializeField]`, «убрать параметр» у `OnTriggerEnter(Collider other)`, `?.` / `??` для `Transform`, `GameObject` | не подключены **Microsoft.Unity.Analyzers**. Visual Studio и VS Code подключают их сами, Unity добавляет их в `.csproj`, только если установлена VS/VS Code | **не применять эти исправления**: `readonly` ломает сериализацию, `?.` на объектах Unity пропускает уничтоженные объекты. Исправление — в плане (docs/RIDER-GAP.md, пункт 1) |
| Ошибка в другом файле не видна, пока его не откроешь (например, после переименования метода) | анализ ведётся только по **открытым файлам** (`dotnet_*_diagnostics_scope = openFiles`, как в VS Code по умолчанию) | Ctrl+Shift+B (сборка решения) показывает ошибки всех файлов. Анализ всего решения — в плане (пункт 2) |
| Нет красных ошибок даже на явной опечатке — или ложные «тип не найден» на рабочем коде; в «Выводе» — предупреждение «не найдено библиотек из .csproj» | `.csproj` ссылаются на удалённую версию Unity или пакеты. UniPy сам подставляет установленную версию из `ProjectVersion.txt` и новые папки пакетов; что не нашлось — перечисляет | Regenerate project files в Unity |
| Серые `using`, нет подсказок Unity, нет ошибок | файл не входит ни в один `.csproj` | Regenerate project files в Unity |
| Ошибки «тип не найден» для пакета, который только что обновили | у пакетов Unity UniPy берёт **готовые DLL** из `Library/ScriptAssemblies` (так быстрее в 25 раз). Пока Unity не перекомпилировал — DLL старая | переключиться в Unity, дождаться компиляции |
| После правок в Unity подсказки устарели | Unity перезаписал `.csproj`, анализатор перечитывает их не сразу | «Средства → Перезапустить анализаторы кода» |

## 6. Журналы

- Журнал анализатора: панель «Вывод» → источник C#.
- Консоль Unity в IDE: читается из `<проект>/Logs/Editor.log`.
- Копия решения для анализатора: `~/.cache/unipy-studio/roslyn/<хэш>/` — её можно удалить,
  UniPy соберёт заново при следующем открытии проекта.
