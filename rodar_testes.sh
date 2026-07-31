#!/usr/bin/env bash
# Roda toda a bateria de testes na ordem correta.
# Algumas suítes usam dados criadas pelas anteriores — por isso a ordem importa.
#
#   ./rodar_testes.sh
#
set -u

: "${DATABASE_URL:?Defina DATABASE_URL antes de rodar}"
export SECRET_KEY="${SECRET_KEY:-teste}"
export APP_USUARIO="${APP_USUARIO:-admin}"
export APP_SENHA="${APP_SENHA:-teste123}"

SUITES=(
  teste_sistema          # fluxo geral, cadastros e rotas
  teste_nlag             # migração do depósito e catálogo de peças
  teste_liberacao        # atendimento do analista
  teste_painel_liberacao # fila do almoxarifado por OS
  teste_relogio          # cronômetro, paleta e responsividade
  teste_cronometro       # ciclo do manutentor
  teste_fluxo            # triagem do líder, ponta a ponta
  teste_recorte          # o que cada perfil enxerga
  teste_perfis           # permissões e pedido de peça
  teste_email            # disparos de e-mail
  teste_email_config     # configuração do envio
  teste_plano_materiais  # necessidade das preventivas
  teste_criticidade      # níveis e matriz
  teste_relatorios       # planilhas e backup
)

falhas=0
echo "═══════════════════════════════════════════════════════════"
for t in "${SUITES[@]}"; do
  printf "%-24s " "$t"
  if python3 "$t.py" > "/tmp/$t.log" 2>&1; then
    grep -E "^✅" "/tmp/$t.log" | tail -1
  else
    echo "❌ FALHOU  —  veja /tmp/$t.log"
    grep -B2 -A4 "AssertionError\|Error" "/tmp/$t.log" | tail -10
    falhas=$((falhas + 1))
  fi
done
echo "═══════════════════════════════════════════════════════════"
if [ "$falhas" -eq 0 ]; then
  echo "✅ ${#SUITES[@]} suítes passaram."
else
  echo "❌ $falhas suíte(s) com falha."
  exit 1
fi
