import json
import os
import time
import pandas as pd
from PIL import Image
import streamlit as st
from google import genai
from google.genai import types

st.set_page_config(page_title="Auditoria de Canhotas vs Fatura", layout="wide")
st.title("🛡️ Painel de Auditoria Operacional - Canhotas vs. Fatura")

api_key = st.secrets.get("GEMINI_API_KEY")
if not api_key:
    st.error("Chave GEMINI_API_KEY não encontrada nos Secrets.")
    st.stop()

client = genai.Client(api_key=api_key)

# Mapeamento de contingência cadastral: Nome -> Matrícula
MAPA_COLABORADORES = {
    "ALDO GOMES": "1002",
}

def processar_com_fallback(client, prompt, contents):
    """
    Executa chamadas à IA com redundância entre modelos da geração 3.x.
    """
    modelos_candidatos = [
        "gemini-3.8-flash",
        "gemini-3.5-flash",
        "gemini-3.1-pro-preview"
    ]
    erros_acumulados = []

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
                msg_erro = str(err)
                erros_acumulados.append(f"[{modelo}] Tentativa {tentativa+1}: {msg_erro}")
                
                if any(code in msg_erro for code in ["503", "429", "UNAVAILABLE"]):
                    time.sleep(2 * (tentativa + 1))
                    continue
                elif "404" in msg_erro or "NOT_FOUND" in msg_erro:
                    break
                else:
                    break

    detalhes = " | ".join(erros_acumulados)
    raise RuntimeError(f"Indisponibilidade na rede de IA. Log: {detalhes}")

# Interface de Entrada de Arquivos
col1, col2 = st.columns(2)
with col1:
    canhotas_files = st.file_uploader(
        "1. Lote de Canhotas (Aceita múltiplos arquivos PDF ou Imagens)",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True
    )
with col2:
    fatura_file = st.file_uploader(
        "2. Fatura / Relatório Mensal (PDF ou Imagem)",
        type=["pdf", "png", "jpg", "jpeg"]
    )

if st.button("🚀 Executar Auditoria Mensal", type="primary"):
    if not canhotas_files or not fatura_file:
        st.warning("Envie o lote de canhotas e a fatura para iniciar a auditoria.")
    else:
        with st.spinner("Etapa 1/2: Analisando Fatura e extraindo itens e preços de contrato..."):
            try:
                # 1. Leitura e Extração da Fatura
                fatura_contents = []
                if fatura_file.type == "application/pdf":
                    fatura_contents.append(types.Part.from_bytes(data=fatura_file.read(), mime_type="application/pdf"))
                else:
                    fatura_contents.append(Image.open(fatura_file))

                prompt_fatura = """
                Analise esta fatura/relatório de serviços/lavanderia e extraia os itens faturados no seguinte JSON estrito:
                {
                    "itens_fatura": [
                        {
                            "item": "nome do item (ex: Macacão, Cinto, etc)",
                            "quantidade": integer_quantidade_total_faturada,
                            "valor_unitario": float_valor_unitario,
                            "valor_total": float_valor_total_do_item
                        }
                    ],
                    "valor_total_fatura": float_valor_total_geral
                }
                """
                dados_fatura = processar_com_fallback(client, prompt_fatura, fatura_contents)

                # 2. Leitura e Extração do Lote de Canhotas
                with st.spinner("Etapa 2/2: Auditando lote de canhotas e cruzando com o cadastro..."):
                    canhotas_identificadas = []
                    canhotas_nao_identificadas = []

                    prompt_canhota = """
                    Analise este documento/imagem e extraia todas as canhotas de entrega presentes.
                    Retorne estritamente um JSON neste formato:
                    {
                        "canhotas": [
                            {
                                "colaborador": "NOME COMPLETO DO COLABORADOR ou N/A se em branco",
                                "matricula": "string apenas números ou N/A se em branco",
                                "itens": [
                                    {
                                        "item": "nome do item (ex: Macacão, Cinto)",
                                        "quantidade": integer_quantidade
                                    }
                                ],
                                "status_assinatura": "descrição breve do traço/assinatura"
                            }
                        ]
                    }
                    """

                    for c_file in canhotas_files:
                        c_contents = []
                        if c_file.type == "application/pdf":
                            c_contents.append(types.Part.from_bytes(data=c_file.read(), mime_type="application/pdf"))
                        else:
                            c_contents.append(Image.open(c_file))

                        res_canhota = processar_com_fallback(client, prompt_canhota, c_contents)
                        lista = res_canhota.get("canhotas", [])

                        for c in lista:
                            nome = str(c.get("colaborador", "")).strip().upper()
                            mat = str(c.get("matricula", "N/A")).strip()

                            # Associação por nome caso a matrícula esteja em branco
                            if mat in ["N/A", "", "None"] and nome in MAPA_COLABORADORES:
                                mat = MAPA_COLABORADORES[nome]

                            c["matricula"] = mat
                            c["colaborador"] = nome if nome else "NÃO IDENTIFICADO"

                            # Segregação: Identificados vs Não Identificados
                            if mat != "N/A" or nome in MAPA_COLABORADORES:
                                canhotas_identificadas.append(c)
                            else:
                                canhotas_nao_identificadas.append(c)

                # Consolidação de Itens da Fatura
                fatura_itens = {}
                for item_f in dados_fatura.get("itens_fatura", []):
                    nome_i = item_f.get("item", "Geral").strip().title()
                    v_unit = float(item_f.get("valor_unitario", 0.0))
                    qtd_f = int(item_f.get("quantidade", 0))
                    fatura_itens[nome_i] = {
                        "valor_unitario": v_unit,
                        "qtd_faturada": qtd_f,
                        "total_faturado": float(item_f.get("valor_total", v_unit * qtd_f))
                    }

                # Consolidação de Itens Apurados nas Canhotas Validadas
                apurado_itens = {}
                for c in canhotas_identificadas:
                    itens_list = c.get("itens", [])
                    if not itens_list and "quantidade_pecas" in c:
                        itens_list = [{"item": "Peça", "quantidade": c.get("quantidade_pecas", 1)}]
                    
                    for it in itens_list:
                        nome_i = it.get("item", "Geral").strip().title()
                        qtd = int(it.get("quantidade", 0))
                        apurado_itens[nome_i] = apurado_itens.get(nome_i, 0) + qtd

                # Construção da Tabela de Batimento Lado a Lado
                todos_itens = sorted(list(set(list(fatura_itens.keys()) + list(apurado_itens.keys()))))
                
                tabela_comparativa = []
                total_apurado_rs = 0.0
                total_faturado_rs = 0.0

                for item_name in todos_itens:
                    info_f = fatura_itens.get(item_name, {"valor_unitario": 0.0, "qtd_faturada": 0, "total_faturado": 0.0})
                    v_unit = info_f["valor_unitario"]
                    
                    if v_unit == 0.0 and fatura_itens:
                        v_unit = list(fatura_itens.values())[0]["valor_unitario"]

                    qtd_apurada = apurado_itens.get(item_name, 0)
                    qtd_faturada = info_f["qtd_faturada"]

                    subtotal_apurado = qtd_apurada * v_unit
                    subtotal_faturado = info_f["total_faturado"] if info_f["total_faturado"] > 0 else (qtd_faturada * v_unit)

                    total_apurado_rs += subtotal_apurado
                    total_faturado_rs += subtotal_faturado

                    status = "✅ Conforme" if qtd_apurada == qtd_faturada and qtd_faturada > 0 else "❌ Divergente"

                    tabela_comparativa.append({
                        "Item Auditado": item_name,
                        "Valor Unit. (Fatura)": f"R$ {v_unit:.2f}",
                        "Qtd. Apurada (Canhotas)": f"{qtd_apurada} un",
                        "Qtd. Faturada (Relatório)": f"{qtd_faturada} un",
                        "Subtotal Apurado": f"R$ {subtotal_apurado:.2f}",
                        "Subtotal Faturado": f"R$ {subtotal_faturado:.2f}",
                        "Status de Conformidade": status
                    })

                # Exibição do Relatório de Auditoria
                st.subheader("📋 Resumo Executivo da Auditoria")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Total Apurado (Validados)", f"R$ {total_apurado_rs:.2f}")
                m2.metric("Total Faturado (Relatório)", f"R$ {total_faturado_rs:.2f}")
                
                diferenca = total_apurado_rs - total_faturado_rs
                m3.metric("Divergência Financeira", f"R$ {diferenca:.2f}", delta_color="inverse")
                
                status_geral = "✅ APROVADO S/ RESSALVAS" if diferenca == 0 and len(canhotas_nao_identificadas) == 0 else "⚠️ REQUER ATENÇÃO / DIVERGENTE"
                m4.metric("Status Geral", status_geral)

                st.markdown("### 📊 Batimento Lado a Lado por Item (Auditado vs. Relatório)")
                st.dataframe(pd.DataFrame(tabela_comparativa), use_container_width=True)

                # Detalhamento por Colaborador Validado
                st.markdown("### 👤 Colaboradores Auditados e Validados")
                if canhotas_identificadas:
                    for c in canhotas_identificadas:
                        mat = c["matricula"]
                        nome = c["colaborador"]
                        itens_txt = ", ".join([f"{it.get('quantidade',0)}x {it.get('item','Item')}" for it in c.get("itens",[])])
                        
                        with st.expander(f"🔹 {nome} (Matrícula: {mat}) - Itens: {itens_txt}"):
                            st.write(f"**Status da Assinatura na Canhota:** {c.get('status_assinatura', 'N/A')}")
                            
                            # Busca Gabaritos
                            gabaritos = []
                            if os.path.exists("gabaritos") and mat != "N/A":
                                for idx in [1, 2, 3]:
                                    path = f"gabaritos/{mat}_{idx}.jpg"
                                    if os.path.exists(path):
                                        gabaritos.append(path)
                            
                            if gabaritos:
                                st.success(f"✓ {len(gabaritos)} Gabarito(s) de assinatura validado(s).")
                                cols = st.columns(len(gabaritos))
                                for i, g_path in enumerate(gabaritos):
                                    cols[i].image(g_path, caption=f"Matriz {i+1}")
                            else:
                                st.warning(f"⚠️ Nenhum gabarito cadastrado na pasta /gabaritos para a matrícula {mat}.")
                else:
                    st.info("Nenhuma canhota validada encontrada.")

                # Painel de Exceções: Colaboradores Não Identificados
                if canhotas_nao_identificadas:
                    st.markdown("---")
                    st.error(f"🚨 **Atenção:** Constam {len(canhotas_nao_identificadas)} canhota(s) de colaboradores NÃO IDENTIFICADOS.")
                    st.caption("Estes itens não foram computados no batimento oficial acima até que sejam regularizados.")
                    
                    for idx, c_ni in enumerate(canhotas_nao_identificadas, 1):
                        itens_ni = ", ".join([f"{it.get('quantidade',0)}x {it.get('item','Item')}" for it in c_ni.get("itens",[])])
                        st.warning(f"Exceção #{idx}: Colaborador Lido: '{c_ni.get('colaborador')}' | Matrícula Lido: '{c_ni.get('matricula')}' | Itens: {itens_ni} | Assinatura: {c_ni.get('status_assinatura')}")

            except Exception as e:
                st.error(f"Falha no processamento da auditoria: {str(e)}")
