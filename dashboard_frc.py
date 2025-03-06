import streamlit as st
import pandas as pd
import mysql.connector
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import os

# Função de conexão com o banco de dados
def conectar_ao_banco():
    return mysql.connector.connect(
        host=st.secrets["DB_HOST"],
        user=st.secrets["DB_USER"],
        password=st.secrets["DB_PASSWORD"],
        database=st.secrets["DB_NAME"]
    )

# Cache de dados para melhorar o desempenho
@st.cache_data(ttl=300)
def carregar_dados():
    conn = conectar_ao_banco()
    
    # Carregar robôs
    robos_df = pd.read_sql("SELECT * FROM robots_tb", conn)
    
    # Carregar desafios
    desafios_df = pd.read_sql("SELECT * FROM challenge_tb", conn)
    
    # Carregar fases
    fases_df = pd.read_sql("SELECT * FROM challenge_phases_tb", conn)
    
    # Carregar pontuações com joins para obter nomes de equipes e informações de desafios
    pontuacoes_df = pd.read_sql("""
        SELECT s.*, r.team, r.location, c.name as challenge_name, c.type as challenge_type
        FROM scores_tb s
        JOIN robots_tb r ON s.robot_id = r.id
        JOIN challenge_tb c ON s.challenge_id = c.id
        JOIN challenge_phases_tb p ON s.phase_id = p.id
    """, conn)
    
    conn.close()
    return robos_df, desafios_df, fases_df, pontuacoes_df

# Calcular classificações de equipes para cada desafio
def calcular_classificacoes(pontuacoes_df):
    # Calcular pontos totais para cada robô em cada desafio
    # Pontos = completed_teleop + completed_autonomous
    classificacoes = pontuacoes_df.groupby(['robot_id', 'team', 'challenge_id', 'challenge_name'])[
        ['completed_teleop', 'completed_autonomous']
    ].sum().reset_index()
    
    classificacoes['pontos_totais'] = classificacoes['completed_teleop'] + classificacoes['completed_autonomous']
    
    # Classificar equipes dentro de cada desafio com base nos pontos totais
    classificacoes['classificacao'] = classificacoes.groupby('challenge_id')['pontos_totais'].rank(ascending=False, method='min')
    
    return classificacoes

# Calcular desempenho por fase
def calcular_desempenho_fase(pontuacoes_df):
    # Agrupar por robô e fase para ver taxa de sucesso
    desempenho_fase = pontuacoes_df.groupby(['robot_id', 'team', 'phase_id', 'phase_name'])[
        ['completed_teleop', 'completed_autonomous']
    ].sum().reset_index()
    
    # Calcular sucesso como binário (completaram a fase ou não)
    desempenho_fase['sucesso'] = ((desempenho_fase['completed_teleop'] > 0) | 
                             (desempenho_fase['completed_autonomous'] > 0)).astype(int)
    
    return desempenho_fase

# Função para construir aliança ótima
def construir_alianca_otima(classificacoes, id_equipe_selecionada=None, tamanho_alianca=3, num_aliancas=10):
    todas_aliancas = []
    equipes_usadas = set()
    
    # Se uma equipe foi selecionada, começamos com ela e procuramos apenas complementos
    if id_equipe_selecionada:
        equipes_usadas.add(id_equipe_selecionada)
        equipes_iniciais = [id_equipe_selecionada]
    else:
        # Se não, tentamos começar com cada equipe disponível
        equipes_iniciais = classificacoes['robot_id'].unique()
    
    for equipe_inicial in equipes_iniciais:
        if len(todas_aliancas) >= num_aliancas:
            break
            
        equipes_disponiveis = classificacoes[~classificacoes['robot_id'].isin(equipes_usadas)].copy()
        alianca_atual = []
        
        # Adicionar equipe inicial
        alianca_atual.append(classificacoes[classificacoes['robot_id'] == equipe_inicial])
        equipes_temp_usadas = {equipe_inicial}
        
        # Construir o resto da aliança
        while len(alianca_atual) < tamanho_alianca and not equipes_disponiveis.empty:
            pontos_fortes_alianca = pd.concat(alianca_atual).groupby('challenge_id')['pontos_totais'].sum().reset_index()
            
            if not pontos_fortes_alianca.empty:
                desafio_mais_fraco = pontos_fortes_alianca.loc[pontos_fortes_alianca['pontos_totais'].idxmin(), 'challenge_id']
                melhor_para_fraqueza = equipes_disponiveis[equipes_disponiveis['challenge_id'] == desafio_mais_fraco]
                
                if not melhor_para_fraqueza.empty:
                    melhor_equipe = melhor_para_fraqueza.sort_values('pontos_totais', ascending=False).iloc[0]
                    id_melhor_equipe = melhor_equipe['robot_id']
                    
                    if id_melhor_equipe not in equipes_temp_usadas:
                        alianca_atual.append(equipes_disponiveis[equipes_disponiveis['robot_id'] == id_melhor_equipe])
                        equipes_temp_usadas.add(id_melhor_equipe)
                        equipes_disponiveis = equipes_disponiveis[~equipes_disponiveis['robot_id'].isin(equipes_temp_usadas)]
                        continue
            
            # Se não encontrou pelo desafio mais fraco, pega a melhor geral
            pontos_totais_por_equipe = equipes_disponiveis.groupby('robot_id')['pontos_totais'].sum()
            if not pontos_totais_por_equipe.empty:
                id_melhor_equipe = pontos_totais_por_equipe.idxmax()
                if id_melhor_equipe not in equipes_temp_usadas:
                    alianca_atual.append(equipes_disponiveis[equipes_disponiveis['robot_id'] == id_melhor_equipe])
                    equipes_temp_usadas.add(id_melhor_equipe)
                    equipes_disponiveis = equipes_disponiveis[~equipes_disponiveis['robot_id'].isin(equipes_temp_usadas)]
            else:
                break
        
        if len(alianca_atual) == tamanho_alianca:
            alianca_completa = pd.concat(alianca_atual)
            pontuacao_total = alianca_completa['pontos_totais'].sum()
            todas_aliancas.append({
                'alianca': alianca_completa,
                'pontuacao_total': pontuacao_total,
                'equipes': list(equipes_temp_usadas)
            })
            
            # Adicionar equipes usadas ao conjunto global apenas se não estiver buscando por uma equipe específica
            if not id_equipe_selecionada:
                equipes_usadas.update(equipes_temp_usadas)
    
    # Ordenar alianças por pontuação total
    todas_aliancas.sort(key=lambda x: x['pontuacao_total'], reverse=True)
    return todas_aliancas[:num_aliancas]

# Dashboard principal
def main():
    st.title("Dashboard de Análise FRC REEFSCAPE")
    
    # Carregar dados
    with st.spinner("Carregando dados..."):
        robos_df, desafios_df, fases_df, pontuacoes_df = carregar_dados()
        classificacoes = calcular_classificacoes(pontuacoes_df)
        desempenho_fase = calcular_desempenho_fase(pontuacoes_df)
    
    # Barra lateral para filtros
    st.sidebar.header("Filtros")
    desafio_selecionado = st.sidebar.selectbox(
        "Selecione o Desafio", 
        options=desafios_df['name'].unique(),
        index=0
    )
    
    alianca_selecionada = st.sidebar.selectbox(
        "Filtro de Aliança",
        options=['Todas', 'Vermelha', 'Azul'],
        index=0
    )
    
    # Aplicar filtros aos dados
    pontuacoes_filtradas = pontuacoes_df.copy()
    if alianca_selecionada != 'Todas':
        alianca_map = {'Vermelha': 'red', 'Azul': 'blue'}
        pontuacoes_filtradas = pontuacoes_filtradas[pontuacoes_filtradas['alliance'] == alianca_map[alianca_selecionada]]
    
    # Abas do dashboard
    aba1, aba2, aba3, aba4 = st.tabs([
        "Classificação das Equipes", 
        "Análise de Desafios", 
        "Construtor de Alianças",
        "Histórico de Partidas"
    ])
    
    with aba1:
        st.header("Classificação das Equipes")
        
        # Tabela de classificação geral
        st.subheader("Desempenho Geral das Equipes")
        classificacao_geral = classificacoes.groupby(['robot_id', 'team'])['pontos_totais'].sum().reset_index()
        classificacao_geral = classificacao_geral.sort_values('pontos_totais', ascending=False)
        classificacao_geral['classificacao'] = classificacao_geral['pontos_totais'].rank(ascending=False, method='min')
        classificacao_geral = classificacao_geral.rename(columns={'pontos_totais': 'Pontos Totais'})
        
        # Exibir as 10 melhores equipes
        st.dataframe(
            classificacao_geral[['classificacao', 'team', 'Pontos Totais']]
            .sort_values('classificacao')
            .head(10)
            .reset_index(drop=True)
        )
        
        # Criar gráfico de barras das 10 melhores equipes
        fig = px.bar(
            classificacao_geral.sort_values('Pontos Totais', ascending=False).head(10),
            x='team',
            y='Pontos Totais',
            title="Top 10 Equipes por Pontos Totais",
            labels={'team': 'Equipe', 'Pontos Totais': 'Pontos Totais'}
        )
        st.plotly_chart(fig)
        
        # Comparação Autônomo vs Teleoperado
        st.subheader("Desempenho Autônomo vs. Teleoperado")
        auto_teleop = pontuacoes_df.groupby('robot_id')[['completed_autonomous', 'completed_teleop']].sum().reset_index()
        auto_teleop = auto_teleop.merge(robos_df[['id', 'team']], left_on='robot_id', right_on='id')
        auto_teleop = auto_teleop.sort_values('completed_autonomous', ascending=False).head(10)
        
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(
            go.Bar(x=auto_teleop['team'], y=auto_teleop['completed_autonomous'], name="Autônomo"),
            secondary_y=False,
        )
        fig.add_trace(
            go.Bar(x=auto_teleop['team'], y=auto_teleop['completed_teleop'], name="Teleoperado"),
            secondary_y=False,
        )
        fig.update_layout(title_text="Top 10 Equipes - Pontos Autônomos vs Teleoperados")
        fig.update_xaxes(title_text="Equipe")
        fig.update_yaxes(title_text="Pontos")
        st.plotly_chart(fig)
    
    with aba2:
        st.header(f"Análise de Desafios: {desafio_selecionado}")
        
        # Filtrar para o desafio selecionado
        id_desafio = desafios_df[desafios_df['name'] == desafio_selecionado]['id'].iloc[0]
        pontuacoes_desafio = pontuacoes_filtradas[pontuacoes_filtradas['challenge_id'] == id_desafio]
        fases_desafio = fases_df[fases_df['challenge_id'] == id_desafio]
        
        # Taxas de sucesso por fase
        st.subheader("Taxas de Sucesso por Fase")
        sucesso_fase = desempenho_fase[desempenho_fase['phase_id'].isin(fases_desafio['id'])]
        sucesso_fase = sucesso_fase.groupby(['phase_name'])['sucesso'].mean().reset_index()
        sucesso_fase['taxa_sucesso'] = sucesso_fase['sucesso'] * 100
        
        fig = px.bar(
            sucesso_fase,
            x='phase_name',
            y='taxa_sucesso',
            title=f"Taxa de Sucesso por Fase para {desafio_selecionado}",
            labels={'phase_name': 'Fase', 'taxa_sucesso': 'Taxa de Sucesso (%)'}
        )
        st.plotly_chart(fig)
        
        # Melhores equipes neste desafio
        st.subheader(f"Melhores Equipes em {desafio_selecionado}")
        classificacoes_desafio = classificacoes[classificacoes['challenge_id'] == id_desafio]
        classificacoes_desafio = classificacoes_desafio.sort_values('pontos_totais', ascending=False)
        
        st.dataframe(
            classificacoes_desafio[['classificacao', 'team', 'pontos_totais']]
            .sort_values('classificacao')
            .head(10)
            .reset_index(drop=True)
        )
        
        # Desempenho por fase
        st.subheader("Desempenho das Equipes por Fase")
        desempenho_equipe_fase = pontuacoes_desafio.groupby(['team', 'phase_name'])[
            ['completed_teleop', 'completed_autonomous']
        ].sum().reset_index()
        desempenho_equipe_fase['total'] = desempenho_equipe_fase['completed_teleop'] + desempenho_equipe_fase['completed_autonomous']
        
        # Obter as 5 melhores equipes
        top_equipes = classificacoes_desafio.head(5)['team'].unique()
        desempenho_equipe_fase = desempenho_equipe_fase[desempenho_equipe_fase['team'].isin(top_equipes)]
        
        fig = px.bar(
            desempenho_equipe_fase,
            x='phase_name',
            y='total',
            color='team',
            barmode='group',
            title=f"Desempenho das 5 Melhores Equipes por Fase em {desafio_selecionado}",
            labels={'phase_name': 'Fase', 'total': 'Pontos Totais', 'team': 'Equipe'}
        )
        st.plotly_chart(fig)
    
    with aba3:
        st.header("Construtor de Alianças")
        
        st.write("""
        Esta ferramenta ajuda você a construir alianças ideais com base em forças complementares
        em diferentes desafios. Selecione uma equipe para construir em torno dela, ou deixe o sistema
        recomendar as melhores alianças possíveis.
        """)
        
        # Seleção de equipe
        opcoes_equipe = [{'label': row['team'], 'value': row['id']} for _, row in robos_df.iterrows()]
        opcoes_equipe = [{'label': 'Automático (Melhores Alianças)', 'value': None}] + opcoes_equipe
        
        id_equipe_selecionada = st.selectbox(
            "Selecione uma equipe para construir a aliança:",
            options=[opt['value'] for opt in opcoes_equipe],
            format_func=lambda x: next((opt['label'] for opt in opcoes_equipe if opt['value'] == x), str(x)),
            index=0
        )
        
        # Construir alianças ótimas
        aliancas_otimas = construir_alianca_otima(classificacoes, id_equipe_selecionada, num_aliancas=10)
        
        # Exibir todas as alianças recomendadas
        st.subheader("Alianças Recomendadas")
        
        for i, alianca in enumerate(aliancas_otimas, 1):
            with st.expander(f"Aliança #{i} - Pontuação Total: {alianca['pontuacao_total']:.2f}"):
                # Mostrar membros da aliança
                equipes = [robos_df[robos_df['id'] == robot_id]['team'].iloc[0] for robot_id in alianca['equipes']]
                st.write(f"Membros: {', '.join(equipes)}")
                
                # Mostrar força por desafio
                forca_alianca = alianca['alianca'].groupby('challenge_name')['pontos_totais'].sum().reset_index()
                
                fig = px.bar(
                    forca_alianca, 
                    x='challenge_name', 
                    y='pontos_totais',
                    title=f"Força da Aliança #{i} por Desafio",
                    labels={'challenge_name': 'Desafio', 'pontos_totais': 'Pontos Combinados'}
                )
                st.plotly_chart(fig)
                
                # Mostrar detalhes das equipes
                st.subheader("Detalhes dos Membros da Aliança")
                detalhes_alianca = alianca['alianca'].pivot(
                    index='team', 
                    columns='challenge_name', 
                    values='pontos_totais'
                ).reset_index()
                
                st.dataframe(detalhes_alianca)
    
    with aba4:
        st.header("Histórico de Partidas")
        
        # Selecionar equipe para visualizar histórico de partidas
        id_equipe_selecionada_historico = st.selectbox(
            "Selecione a Equipe para Visualizar o Histórico de Partidas",
            options=robos_df['id'].tolist(),
            format_func=lambda x: robos_df[robos_df['id'] == x]['team'].iloc[0],
            key="equipe_historico_partidas"
        )
        
        # Obter histórico de partidas da equipe
        partidas_equipe = pontuacoes_filtradas[pontuacoes_filtradas['robot_id'] == id_equipe_selecionada_historico]
        
        if not partidas_equipe.empty:
            # Agrupar por partida e calcular pontos totais
            resumo_partida = partidas_equipe.groupby('match_id')[
                ['completed_teleop', 'completed_autonomous']
            ].sum().reset_index()
            resumo_partida['pontos_totais'] = resumo_partida['completed_teleop'] + resumo_partida['completed_autonomous']
            
            # Mostrar gráfico de histórico de partidas
            fig = px.line(
                resumo_partida.sort_values('match_id'),
                x='match_id',
                y='pontos_totais',
                title=f"Histórico de Partidas para {robos_df[robos_df['id'] == id_equipe_selecionada_historico]['team'].iloc[0]}",
                markers=True,
                labels={'match_id': 'ID da Partida', 'pontos_totais': 'Pontos Totais'}
            )
            st.plotly_chart(fig)
            
            # Mostrar dados detalhados das partidas
            st.subheader("Detalhes das Partidas")
            detalhes_partida = partidas_equipe[['match_id', 'phase_name', 'completed_teleop', 'completed_autonomous', 'broke']]
            detalhes_partida['total'] = detalhes_partida['completed_teleop'] + detalhes_partida['completed_autonomous']
            detalhes_partida = detalhes_partida.sort_values(['match_id', 'phase_name'])
            
            # Tradução das colunas
            detalhes_partida = detalhes_partida.rename(columns={
                'match_id': 'ID da Partida',
                'phase_name': 'Nome da Fase',
                'completed_teleop': 'Teleoperado Completado',
                'completed_autonomous': 'Autônomo Completado',
                'broke': 'Quebrou',
                'total': 'Total'
            })
            
            st.dataframe(detalhes_partida)
            
            # Mostrar divisão de pontos por fase ao longo do tempo
            st.subheader("Desempenho por Fase ao Longo do Tempo")
            fase_ao_longo_tempo = partidas_equipe.groupby(['match_id', 'phase_name'])[
                ['completed_teleop', 'completed_autonomous']
            ].sum().reset_index()
            fase_ao_longo_tempo['total'] = fase_ao_longo_tempo['completed_teleop'] + fase_ao_longo_tempo['completed_autonomous']
            
            fig = px.line(
                fase_ao_longo_tempo,
                x='match_id',
                y='total',
                color='phase_name',
                title="Desempenho por Fase ao Longo do Tempo",
                markers=True,
                labels={'match_id': 'ID da Partida', 'total': 'Pontos', 'phase_name': 'Fase'}
            )
            st.plotly_chart(fig)
        else:
            st.write("Nenhum dado de partida disponível para a equipe selecionada.")

if __name__ == "__main__":
    main()