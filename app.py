import json
import os
import time
import re
import unicodedata
import pandas as pd
from PIL import Image
import streamlit as st
from google import genai
from google.genai import types

st.set_page_config(page_title="Auditoria de Canhotas v1.0", layout="wide")
st.title("🛡️ Painel de Auditoria Operacional e Grafotécnica (v1.0 - Por Nome)")

api_key = st.secrets.get("GEMINI_API_KEY")
if not api_key:
    st.error("Chave GEMINI_API_KEY não encontrada nos Secrets do Streamlit.")
    st.stop()

client = genai.Client(api_key=api_key)

# Lista Oficial de Colaboradores Cadastrados
COLABORADORES_CADASTRADOS = [
    "ALDO GOMES",
    "RICARDO TEIXEIRA",
    "CLEITON"
]

def normalizar_nome_arquivo(texto):
    """Converte 'Aldo Gomes' em 'ALDO_GOMES' para busca de arquivos."""
    if not texto:
        return "DESCONHECIDO"
    texto_norm = unicodedata.normalize('NFD', texto).encode('ascii', 'ignore').decode("utf-8")
    texto_limpo = re.sub(r'[^A-Z0-9 ]', '', texto_norm.upper().strip())
    return texto_limpo.replace(" ", "_")

def identificar_colaborador(nome_lido):
    """Mapeia variações de leitura para a lista oficial cadastrada."""
    nome_norm = normalizar_nome_arquivo(nome_lido).replace("_", " ")
    for colab in COLABORADORES_CADASTRADOS:
        if colab in nome_norm or nome_norm in colab:
            return colab
    return None

def processar_com_fallback(client, prompt, contents):
    # Modelos atualizados e suportados na API
    modelos_candidatos = ["gemini-1.5-flash", "gemini-1.5-pro"]
    erros = []

    for modelo in modelos_candidatos:
        for tentativa in range(3):
            try:
                response = client.models.generate_content(
                    model=modelo,
                    contents=[prompt] + contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    ),
                )
                return json.loads(response.text)
            except Exception as err:
                msg = str(err)
                erros.append(f"[{modelo}] {msg}")
                if any(code in msg for code in ["503", "429", "RESOURCE_EXHAUSTED"]):
                    time.sleep(2 * (tentativa + 1))
                    continue
                break
    raise RuntimeError("Falha na comunicação com a API Gemini. Log: " + " | ".join(erros))

# Interface de Entrada
col1, col2 = st.columns(2)
with col1:
    canhotas_files = st.file_uploader("1. Lote de Canhotas (PDF ou Imagem)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)
with col2:
    fatura_file = st.file_uploader("2. Fatura / Relatório Mensal", type=["pdf", "png", "jpg", "jpeg"])

if st.button("🚀 Executar Auditoria", type="primary"):
    if not canhotas_files or not fatura_file:
        st.warning("Envie as canhotas e a fatura para iniciar.")
    else:
        # Etapa 1: Processar Fatura
        with st.spinner("Analisando Fatura e extraindo itens..."):
            fatura_contents = []
            if fatura_file.type == "application/pdf":
                fatura_contents.append(types.Part.from_bytes(data=fatura_file.read(), mime_type="application/pdf"))
            else:
                fatura_contents.append(Image.open(fatura_file))

            prompt_fatura = """
            Extraia os itens faturados no formato JSON estrito:
            {
                "itens_fatura": [
                    {"item": "nome do item", "quantidade": 0, "valor_unitario": 0.0, "valor_total": 0.0}
                ],
                "valor_total_fatura": 0.0
            }
            """
            dados_fatura = processar_com_fallback(client, prompt_fatura, fatura_contents)

        # Etapa 2: Processar Canhotas e Checar Assinatura
        with st.spinner("Analisando canhotas e executando perícia grafotécnica..."):
            canhotas_auditadas = []

            for c_file in canhotas_files:
                # Restaura o ponteiro caso o arquivo tenha sido lido previamente
                c_file.seek(0)
                if c_file.type == "application/pdf":
                    c_contents = [types.Part.from_bytes(data=c_file.read(), mime_type="application/pdf")]
                else:
                    c_contents = [Image.open(c_file)]

                prompt_leitura = """
                Analise esta canhota e extraia:
                {
                    "colaborador": "NOME COMPLETO LIDO no papel ou N/A",
                    "itens": [
                        {"item": "nome do item", "quantidade": 1}
                    ]
                }
                """
                res_leitura = processar_com_fallback(client, prompt_leitura, c_contents)
                
                nome_lido = res_leitura.get("colaborador", "N/A")
                colaborador_oficial = identificar_colaborador(nome_lido)

                # Busca o gabarito pelo nome normalizado
                status_grafotecnico = "SEM_GABARITO"
                detalhe_grafotecnico = "Sem imagem de referência na pasta /gabaritos."
                gabarito_path = None

                if colaborador_oficial:
                    chave_arquivo = normalizar_nome_arquivo(colaborador_oficial)
                    path_tentativa = f"gabaritos/{chave_arquivo}.jpg"

                    if os.path.exists(path_tentativa):
                        gabarito_path = path_tentativa
                        gabarito_img = Image.open(gabarito_path)

                        prompt_grafotecnico = """
                        Compare a ASSINATURA presente no documento de canhota com a ASSINATURA DO GABARITO DE REFERÊNCIA.
                        Responda em JSON estrito:
                        {
                            "resultado_assinatura": "CONFORME" ou "SUSPEITA_DIVERGENCIA" ou "AUSENTE",
                            "justificativa": "resumo direto dos traços e semelhança observada"
                        }
                        """
                        res_grafo = processar_com_fallback(client, prompt_grafotecnico, c_contents + [gabarito_img])
                        status_grafotecnico = res_grafo.get("resultado_assinatura", "SUSPEITA_DIVERGENCIA")
                        detalhe_grafotecnico = res_grafo.get("justificativa", "")

                canhotas_auditadas.append({
                    "colaborador_lido": nome_lido,
                    "colaborador_oficial": colaborador_oficial if colaborador_oficial else "NÃO CADASTRADO",
                    "itens": res_leitura.get("itens", []),
                    "status_grafotecnico": status_grafotecnico,
                    "detalhe_grafotecnico": detalhe_grafotecnico,
                    "gabarito_path": gabarito_path
                })

        # Exibição dos Resultados
        st.subheader("📋 Resultado da Auditoria Operacional")
        
        for idx, c_audit in enumerate(canhotas_auditadas, 1):
            st_icon = "✅" if c_audit["status_grafotecnico"] == "CONFORME" else "⚠️"
            with st.expander(f"{st_icon} Canhota #{idx} - Colaborador: {c_audit['colaborador_oficial']} (Lido: '{c_audit['colaborador_lido']}')"):
                st.write(f"**Parecer Grafotécnico da IA:** {c_audit['status_grafotecnico']}")
                st.caption(f"Detalhes: {c_audit['detalhe_grafotecnico']}")
                
                if c_audit["gabarito_path"]:
                    st.image(c_audit["gabarito_path"], width=280, caption=f"Gabarito Oficial ({c_audit['colaborador_oficial']})")
                else:
                    st.warning(f"Para habilitar a validação automática deste colaborador, salve o gabarito como `gabaritos/{normalizar_nome_arquivo(c_audit['colaborador_lido'])}.jpg` no GitHub.")
