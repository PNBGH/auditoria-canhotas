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

st.set_page_config(page_title="Auditoria Automatizada de Canhotas", layout="wide")
st.title("🛡️ Sistema de Auditoria Grafotécnica e Financeira")

api_key = st.secrets.get("GEMINI_API_KEY")
if not api_key:
    st.error("Chave GEMINI_API_KEY não encontrada nos Secrets.")
    st.stop()

client = genai.Client(api_key=api_key)

# Cadastro Centralizado de Colaboradores (Nome Normalizado -> Matrícula)
MAPA_COLABORADORES = {
    "ALDO GOMES": "1002",
    "RICARDO TEIXEIRA": "1003",
    "CLEITON": "1004"
}

def normalizar_texto(texto):
    if not texto:
        return ""
    texto = unicodedata.normalize('NFD', texto).encode('ascii', 'ignore').decode("utf-8")
    return re.sub(r'[^A-Z0-9 ]', '', texto.upper().strip())

def processar_com_fallback(client, prompt, contents):
    modelos_candidatos = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]
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
    raise RuntimeError("Falha na comunicação com a IA. Detalhes: " + " | ".join(erros))

# Interface
col1, col2 = st.columns(2)
with col1:
    canhotas_files = st.file_uploader("1. Canhotas Recebidas", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)
with col2:
    fatura_file = st.file_uploader("2. Fatura / Relatório Mensal", type=["pdf", "png", "jpg", "jpeg"])

if st.button("🚀 Executar Auditoria de Precisão", type="primary"):
    if not canhotas_files or not fatura_file:
        st.warning("Envie os arquivos para iniciar a auditoria.")
    else:
        with st.spinner("Analisando Fatura e Contrato..."):
            fatura_contents = []
            if fatura_file.type == "application/pdf":
                fatura_contents.append(types.Part.from_bytes(data=fatura_file.read(), mime_type="application/pdf"))
            else:
                fatura_contents.append(Image.open(fatura_file))

            prompt_fatura = """
            Extraia os itens faturados no formato JSON estrito:
            {
                "itens_fatura": [
                    {"item": "nome", "quantidade": 0, "valor_unitario": 0.0, "valor_total": 0.0}
                ],
                "valor_total_fatura": 0.0
            }
            """
            dados_fatura = processar_com_fallback(client, prompt_fatura, fatura_contents)

        with st.spinner("Processando Auditoria Grafotécnica nas Canhotas..."):
            canhotas_auditadas = []

            for c_file in canhotas_files:
                c_image = Image.open(c_file) if c_file.type != "application/pdf" else None
                c_contents = [types.Part.from_bytes(data=c_file.read(), mime_type="application/pdf")] if c_file.type == "application/pdf" else [c_image]

                # Etapa A: Leitura do Nome/Matrícula
                prompt_leitura = """
                Analise esta canhota e extraia:
                {
                    "colaborador": "NOME DO COLABORADOR ou N/A",
                    "matricula": "Apenas números ou N/A",
                    "itens": [{"item": "nome", "quantidade": 1}]
                }
                """
                res_leitura = processar_com_fallback(client, prompt_leitura, c_contents)
                
                nome_bruto = res_leitura.get("colaborador", "")
                nome_norm = normalizar_texto(nome_bruto)
                mat = str(res_leitura.get("matricula", "N/A")).strip()

                # Busca de Matrícula por Inteligência de Cadastro
                if mat in ["N/A", "", "None"]:
                    for nome_cad, mat_cad in MAPA_COLABORADORES.items():
                        if nome_cad in nome_norm or nome_norm in nome_cad:
                            mat = mat_cad
                            break

                # Etapa B: Comparação Grafotécnica com Gabarito
                gabarito_path = f"gabaritos/{mat}_1.jpg"
                status_grafotecnico = "SEM_GABARITO"
                detalhe_grafotecnico = "Nenhum gabarito oficial cadastrado no repositório."

                if os.path.exists(gabarito_path):
                    gabarito_img = Image.open(gabarito_path)
                    
                    prompt_grafotecnico = """
                    Você é um perito grafotécnico. Compare a ASSINATURA presente na CANHOTA com a ASSINATURA DO GABARITO OFICIAL fornecida.
                    Responda estritamente no formato JSON:
                    {
                        "resultado_assinatura": "CONFORME" ou "SUSPEITA_DIVERGENCIA" ou "AUSENTE",
                        "justificativa": "breve explicação dos traços, inclinação ou divergência observada"
                    }
                    """
                    # Envia a canhota e a imagem de gabarito para a IA comparar
                    inputs_comparacao = c_contents + [gabarito_img]
                    res_grafo = processar_com_fallback(client, prompt_grafotecnico, inputs_comparacao)
                    
                    status_grafotecnico = res_grafo.get("resultado_assinatura", "SUSPEITA_DIVERGENCIA")
                    detalhe_grafotecnico = res_grafo.get("justificativa", "")

                canhotas_auditadas.append({
                    "colaborador": nome_norm if nome_norm else "NÃO IDENTIFICADO",
                    "matricula": mat,
                    "itens": res_leitura.get("itens", []),
                    "status_grafotecnico": status_grafotecnico,
                    "detalhe_grafotecnico": detalhe_grafotecnico,
                    "gabarito_path": gabarito_path if os.path.exists(gabarito_path) else None
                })

        # Exibição do Painel
        st.subheader("📊 Painel Executivo de Auditoria")
        
        # Alertas Grafotécnicos (Exceções)
        suspeitas = [c for c in canhotas_auditadas if c["status_grafotecnico"] == "SUSPEITA_DIVERGENCIA"]
        if suspeitas:
            st.error(f"🚨 **ALERTA GRAFOTÉCNICO:** {len(suspeitas)} assinatura(s) apresentaram divergência com o gabarito oficial!")
            for s in suspeitas:
                st.warning(f" Colaborador: **{s['colaborador']}** (Matrícula: {s['matricula']}) | Parecer da IA: {s['detalhe_grafotecnico']}")

        # Exibição Detalhada por Funcionário
        st.markdown("### 👤 Detalhamento dos Colaboradores e Gabaritos")
        for item_aud in canhotas_auditadas:
            st_color = "✅" if item_aud["status_grafotecnico"] == "CONFORME" else "⚠️"
            with st.expander(f"{st_color} {item_aud['colaborador']} (Matrícula: {item_aud['matricula']}) - Status Assinatura: {item_aud['status_grafotecnico']}"):
                st.write(f"**Análise Grafotécnica da IA:** {item_aud['detalhe_grafotecnico']}")
                if item_aud["gabarito_path"]:
                    st.image(item_aud["gabarito_path"], width=300, caption="Gabarito Oficial Cadastrado")
