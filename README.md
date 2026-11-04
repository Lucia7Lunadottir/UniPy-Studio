# UniPy Studio

A code editor in the spirit of Visual Studio for **Unity (C#)** and **Python / PyQt6** on Linux.
Written in Python + PyQt6.

*Русское описание — ниже. [Быстро подключить Unity](#быстрое-подключение-к-unity).*

![Dark theme](shots/screenshot-dark.png)

## Features

- **C# for Unity through Roslyn** — the same engine as IntelliSense in Visual Studio. Reads the
  `.sln` / `.csproj` files Unity generates: completion, parameter info, hover docs, errors,
  Ctrl+click / F12 into your code and into Unity classes (`MonoBehaviour → Behaviour → Component → Object`).
- **Python through pyright** (the core of Pylance) with checks tuned for PyQt6: red only for
  near-certain bugs (unknown names, missing attributes on your own class), no "may be None" noise.
  Falls back to jedi when pyright is not installed.
- **Visual Studio shortcuts**: F12 / Ctrl+click, Ctrl+− / Ctrl+Shift+− (back / forward),
  Shift+F12 (find all references), Ctrl+R, Ctrl+R / F2 (rename), Ctrl+Space, Ctrl+Shift+Space, F5.
- **Solution Explorer shows code only**, like Visual Studio; "Show all files" toggles the full view.
- **Keeps file encodings** (UTF-8, UTF-8 with BOM, Windows-1251) and line endings byte for byte.
- **Themes**: own dark and light themes in the Visual Studio style; light/dark follows KDE or is
  chosen manually. With [equestria-qt-kit](https://github.com/Lucia7Lunadottir) 1.2+ — Equestria OS
  character themes as well.
- **Analyzer updates only on request** (Help → Analyzer updates): the new version is self-tested
  and rolled back automatically if it does not work.

![Light theme](shots/screenshot-light.png)

## Requirements

- Python 3.9+, PyQt6, jedi (`python`, `python-pyqt6`, `python-jedi` on Arch)
- Optional, for C#: .NET SDK and the Roslyn language server
  `dotnet tool install --global roslyn-language-server --prerelease`
- Optional, for Python errors: Node.js and pyright
  `npm install -g --prefix ~/.local pyright`
- Optional: `equestria-qt-kit` 1.2+ for character themes

Both analyzers are official Microsoft packages (NuGet / npm). The editor finds them in
`~/.dotnet/tools` and `~/.local/bin`.

## Run

```sh
./start.sh                          # or: python3 main.py
./start.sh "/path/to/Unity project" # open a folder or a file right away
```

For a Unity project, Unity must have generated the `.sln` / `.csproj` files
(the Visual Studio or Rider package in Packages — present in new projects by default).

## Arch Linux package

```sh
cd packaging && makepkg -fc
sudo pacman -U "$(makepkg --packagelist)"
```

---

# UniPy Studio (по-русски)

Редактор кода в духе Visual Studio для **Unity (C#)** и **Python / PyQt6** на Linux.

- **C# для Unity через Roslyn** — тот же движок, что IntelliSense в Visual Studio: подсказки,
  параметры, справка, ошибки, Ctrl+клик / F12 в свой код и в классы Unity.
- **Python через pyright** с проверками под PyQt6: красным — только почти наверняка ошибки.
  Без pyright — подсказки jedi.
- **Клавиши Visual Studio**: F12 / Ctrl+клик, Ctrl+− / Ctrl+Shift+− (назад / вперёд),
  Shift+F12 (все ссылки), Ctrl+R, Ctrl+R / F2 (переименовать).
- **Обозреватель решений показывает только код**, как Visual Studio; «Показать все файлы» — всё.
- **Кодировки файлов сохраняются** (UTF-8, UTF-8 с BOM, Windows-1251) и переводы строк — байт в байт.
- **Темы**: свои тёмная и светлая в стиле Visual Studio, светлая/тёмная — как в KDE или вручную;
  с `equestria-qt-kit` 1.2+ — ещё и темы персонажей Equestria OS.
- **Анализаторы обновляются только по кнопке** («Справка → Обновления анализаторов»), с самопроверкой
  и автоматическим откатом.

Запуск: `./start.sh` или `python3 main.py`. Установка анализаторов — команды в разделе
«Requirements» выше.

## Быстрое подключение к Unity

1. Соберите и установите свежий пакет UniPy Studio командами из раздела
   **Arch Linux package** выше.
2. В Unity откройте **Window → Package Manager → + → Install package from disk…**
   (в старых версиях — **Add package from disk…**).
3. Выберите `/usr/share/unipy-studio/unity/com.unipy.ide/package.json`.
   Для запуска из исходников пакет находится в `unity/com.unipy.ide/package.json`.
4. Дождитесь установки зависимости **Visual Studio Editor** и окончания компиляции.
   Саму Visual Studio устанавливать не нужно.
5. Откройте **Edit → Preferences → External Tools → External Script Editor**
   и выберите **UniPy Studio**. Искать исполняемый файл через Browse не требуется.
6. Дважды нажмите на C#-скрипт в Unity: UniPy откроет весь Unity-проект и этот файл.
   Двойной клик по ошибке в Console передаст также строку и столбец.

Unity-пакет добавляется **один раз в каждый проект**; его можно включить в свой
шаблон Unity-проекта. Установка Linux-пакета сама по себе не регистрирует редактор
внутри Unity. Если UniPy не появился в списке, проверьте ошибки компиляции в Console
и наличие `/usr/bin/unipy-studio`, затем переоткройте Preferences.

Интеграция использует штатный `IExternalCodeEditor`, а официальный Unity Visual
Studio Editor генерирует `.sln`/`.csproj`. UniPy получает корень проекта, файл,
строку и столбец; последующие открытия передаются через локальный сокет в окно
того же проекта, не сбрасывая вкладки. Отдельные проекты открываются отдельно.

Ручная проверка после установки:

```sh
unipy-studio --project "/path/to/Unity project" --file "/path/to/Unity project/Assets/Player.cs" --line 12 --column 3
```

Подробности и запасной вариант без Unity-пакета: `unity/com.unipy.ide/README.md`.

## Память и журналы

Панели вывода и терминала хранят до 4000 блоков и примерно миллиона символов
каждая; старый текст удаляется автоматически, в том числе при выводе без переносов
строк; слишком длинные строки сохраняются хвостом до 8192 символов. История панели вывода также ограничена миллионом символов, а сохранённый
stderr программы для разбора traceback — 256 тысячами символов. Это ограничения
текста, а не общий лимит RAM: Qt, открытые файлы и процессы анализаторов используют
дополнительную память. Для полного журнала перенаправляйте вывод программы в файл.

Проверки из корня исходников:

```sh
python3 -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen python3 tests/memory_probe.py
```

Проба памяти измеряет RSS самого редактора на Linux: шесть циклов вывода,
открытия/закрытия файлов, переключения проектов и запуска коротких команд.
Внешние анализаторы в ней отключены для изоляции; это не измерение памяти Roslyn,
pyright или Unity и не гарантия отсутствия утечек при любой нагрузке. Жизненный
цикл объектов анализаторов, удаление обработчиков отменённых запросов и сборка
Python-объектов дополнительно проверяются модульными тестами.

### Если в мониторе растёт вся группа UniPy

Память группы включает Python-интерфейс, Roslyn
(`Microsoft.CodeAnalysis.LanguageServer`), его оболочку и процессы `dotnet`.
Замер одного `python3` не показывает расход C#-анализатора. Внешне редактор может
простаивать, пока Roslyn ещё загружает и анализирует решение.

UniPy передаёт Roslyn область фоновой диагностики `openFiles` (как C# в VS Code):
проверяются открытые файлы, а решение остаётся загруженным для подсказок и переходов.
Для запускаемого анализатора отключено сохранение узлов MSBuild
(`MSBUILDDISABLENODEREUSE=1`, `DOTNET_CLI_USE_MSBUILD_SERVER=0`), по умолчанию выбран
Workstation GC (`DOTNET_gcServer=0`). Явное значение `DOTNET_gcServer` из окружения
сохраняется. Это настройки затрат памяти/производительности, а не жёсткий лимит RAM
и не доказательство исправления утечки внутри Roslyn.

При остановке анализатора на поддерживаемом Unix/Qt завершение ограничивается
созданной UniPy отдельной группой процессов: оставшиеся потомки принудительно
завершаются после попытки штатного shutdown. Чужие процессы `dotnet` по имени
не завершаются. Новые настройки применяются после перезапуска UniPy.

Для отдельного замера Roslyn на временной копии метаданных решения:

```sh
python3 tests/roslyn_idle_probe.py "/path/to/Unity project" --seconds 180
# Для сравнения без настроек экономии:
python3 tests/roslyn_idle_probe.py "/path/to/Unity project" --seconds 180 --baseline
```

Проба читает исходники, но не изменяет Unity-проект; выводит RSS клиента и всех
найденных потомков анализатора. Предупреждения о неразрешённых зависимостях нужно
учитывать: такая копия не всегда полностью воспроизводит окружение исходного
проекта. Готовность LSP сама по себе не означает успешную загрузку всех зависимостей.

В сборке `0.1.0-7` исправлена петля запросов диагностики: применение ответа LSP
обновляет подсветку ошибок, не отправляя следующий запрос. Проверка по изменению
текста, явная проверка и запрос обновления от сервера продолжают работать.
Сам процесс Roslyn остаётся запущенным для подсказок и переходов; его присутствие
в фоне само по себе не означает непрерывный опрос. Исправление покрыто тестом
реального пути «ответ сервера → окно → редактор».
