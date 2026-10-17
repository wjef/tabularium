# Translations

English is the source language (message ids). To add a language, e.g. Polish:

```powershell
$py = "$env:LOCALAPPDATA\Tabularium\venv\Scripts\python.exe"
& $py -m babel.messages.frontend extract -F babel.cfg -o src/tabularium/locales/messages.pot .
& $py -m babel.messages.frontend init -i src/tabularium/locales/messages.pot -d src/tabularium/locales -l pl
# translate src/tabularium/locales/pl/LC_MESSAGES/messages.po, then:
& $py -m babel.messages.frontend compile -d src/tabularium/locales
```
