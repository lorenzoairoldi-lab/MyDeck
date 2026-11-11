#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
mkdir -p -- "$install_dir"
if [[ -e "$install_dir/mydeck" && ! -L "$install_dir/mydeck" ]]; then
  echo "Esiste già $install_dir/mydeck: installazione interrotta per preservarlo." >&2
  exit 1
fi
if [[ -L "$install_dir/mydeck" && "$(readlink -- "$install_dir/mydeck")" != "$project_dir/bin/mydeck" ]]; then
  echo "Il comando mydeck punta già a un altro programma: installazione interrotta." >&2
  exit 1
fi
chmod +x "$project_dir/bin/mydeck"
ln -sfn -- "$project_dir/bin/mydeck" "$install_dir/mydeck"
echo "MyDeck installato: $install_dir/mydeck"
case ":$PATH:" in
  *":$install_dir:"*) echo 'Avvia con: mydeck' ;;
  *) printf 'Aggiungi questa riga al profilo della tua shell:\nexport PATH="%s:$PATH"\n' "$install_dir" ;;
esac
