import streamlit as st
import pandas as pd
import mysql.connector
import plotly.express as px
import plotly.graph_objects as go
import io
import time

# Page configuration
st.set_page_config(
    page_title="FRC REEFSCAPE Dashboard",
    page_icon="🤖",
    layout="wide"
)
# Point mapping
POINTS_MAP = {
    'LEAVE': {'auto': 3, 'teleop': 0},
    'L1': {'auto': 3, 'teleop': 2},      # CORAL L1
    'L2': {'auto': 4, 'teleop': 3},      # CORAL L2
    'L3': {'auto': 6, 'teleop': 4},      # CORAL L3
    'L4': {'auto': 7, 'teleop': 5},      # CORAL L4
    'PROCESSOR': {'auto': 6, 'teleop': 6},
    'NET': {'auto': 4, 'teleop': 4},
    'PARK': {'auto': 0, 'teleop': 2},
    'SHALLOW_CAGE': {'auto': 6, 'teleop': 6},
    'DEEP_CAGE': {'auto': 12, 'teleop': 12}
}

def conectar_ao_banco():
    return mysql.connector.connect(
        host=st.secrets["DB_HOST"],
        user=st.secrets["DB_USER"],
        password=st.secrets["DB_PASSWORD"],
        database=st.secrets["DB_NAME"]
    )

@st.cache_data(ttl=600)  # Increase cache time to 10 minutes
def carregar_dados():
    conn = conectar_ao_banco()
    
    # Query to get all scores with robot and phase information
    query = """
    SELECT 
        r.id as robot_id,
        r.team,
        c.id as challenge_id,
        c.name as challenge_name,
        cp.id as phase_id,
        cp.name as phase_name,
        s.completed_autonomous,
        s.completed_teleop,
        r.location,
        r.alliance
    FROM scores_tb s
    JOIN robots_tb r ON s.robot_id = r.id
    JOIN challenge_tb c ON s.challenge_id = c.id
    JOIN challenge_phases_tb cp ON s.phase_id = cp.id
    """
    
    # Load data into DataFrame
    df = pd.read_sql(query, conn)
    conn.close()
    
    return df

@st.cache_data(ttl=600)
def processar_dados(df):
    # Transform phase_name to match POINTS_MAP keys if needed
    phase_mapping = {
        'CORAL L1': 'L1',
        'CORAL L2': 'L2',
        'CORAL L3': 'L3',
        'CORAL L4': 'L4',
        'BARGE': 'PARK'
        # Add other mappings if necessary
    }
    df['phase_key'] = df['phase_name'].map(phase_mapping).fillna(df['phase_name'])
    
    # Calculate points - first create empty columns
    df['auto_points'] = 0.0
    df['teleop_points'] = 0.0
    
    # Vectorize operations where possible instead of looping
    for phase, points in POINTS_MAP.items():
        mask = df['phase_key'] == phase
        df.loc[mask, 'auto_points'] = df.loc[mask, 'completed_autonomous'] * points['auto']
        df.loc[mask, 'teleop_points'] = df.loc[mask, 'completed_teleop'] * points['teleop']
    
    # Calculate total points
    df['total_points'] = df['auto_points'] + df['teleop_points']
    
    return df

@st.cache_data(ttl=600)
def calcular_rankings(df):
    # Calculate team rankings
    team_rankings = df.groupby('team').agg({
        'auto_points': 'sum',
        'teleop_points': 'sum',
        'total_points': 'sum'
    }).reset_index()
    
    team_rankings['rank'] = team_rankings['total_points'].rank(ascending=False, method='min').astype(int)
    team_rankings = team_rankings.sort_values('rank')
    
    # Calculate challenge-specific rankings
    challenge_rankings = df.groupby(['team', 'challenge_name']).agg({
        'auto_points': 'sum',
        'teleop_points': 'sum', 
        'total_points': 'sum'
    }).reset_index()
    
    return team_rankings, challenge_rankings

@st.cache_data(ttl=600)
def construir_alianca_otima(team_rankings, challenge_rankings, df_processed, tamanho_alianca=3, max_teams=30):
    """Optimized alliance builder that considers phase-specific performance within challenges"""
    start_time = time.time()
    
    # Limit to top teams for better performance
    top_teams = team_rankings.sort_values('total_points', ascending=False).head(max_teams)['team'].tolist()
    available_teams = set(top_teams)
    
    aliances = []
    
    # Create phase-level performance data with challenge context
    phase_performance = df_processed[df_processed['team'].isin(top_teams)].groupby(
        ['team', 'challenge_name', 'phase_name']
    ).agg({
        'total_points': 'sum',
        'completed_autonomous': 'sum',
        'completed_teleop': 'sum'
    }).reset_index()
    
    # Create a dictionary to store best phases for each team in each challenge
    team_challenge_phases = {}
    for team in top_teams:
        team_data = phase_performance[phase_performance['team'] == team]
        team_challenge_phases[team] = {}
        
        # Group by challenge to find best phases within each challenge
        for challenge in team_data['challenge_name'].unique():
            challenge_phases = team_data[team_data['challenge_name'] == challenge]
            # Sort phases by total points to get best performing phases
            best_phases = challenge_phases.sort_values('total_points', ascending=False)
            team_challenge_phases[team][challenge] = {
                'phases': [
                    {
                        'name': row['phase_name'],
                        'points': row['total_points'],
                        'completions': row['completed_autonomous'] + row['completed_teleop']
                    }
                    for _, row in best_phases.iterrows()
                ]
            }
    
    def calculate_alliance_synergy(current_alliance, candidate):
        """Calculate how well a candidate complements the current alliance based on phase-specific strengths"""
        if not current_alliance:
            # For first team, consider their overall phase coverage
            candidate_phases = team_challenge_phases[candidate]
            total_score = 0
            for challenge, data in candidate_phases.items():
                if data['phases']:
                    # Consider their best phase in each challenge
                    total_score += max(phase['points'] for phase in data['phases'])
            return total_score
        
        # Get current alliance's best phases for each challenge
        alliance_coverage = {}
        for team in current_alliance:
            team_phases = team_challenge_phases[team]
            for challenge, data in team_phases.items():
                if challenge not in alliance_coverage:
                    alliance_coverage[challenge] = {'covered_phases': set(), 'max_points': {}}
                
                for phase in data['phases']:
                    phase_name = phase['name']
                    alliance_coverage[challenge]['covered_phases'].add(phase_name)
                    current_max = alliance_coverage[challenge]['max_points'].get(phase_name, 0)
                    alliance_coverage[challenge]['max_points'][phase_name] = max(current_max, phase['points'])
        
        # Calculate how well candidate complements the alliance
        synergy_score = 0
        candidate_phases = team_challenge_phases[candidate]
        
        for challenge, data in candidate_phases.items():
            if not data['phases']:
                continue
                
            # Check each phase the candidate is good at
            for phase in data['phases']:
                phase_name = phase['name']
                phase_points = phase['points']
                
                if challenge not in alliance_coverage:
                    # Candidate brings entirely new challenge capability
                    synergy_score += phase_points * 1.5  # Bonus for new challenge coverage
                elif phase_name not in alliance_coverage[challenge]['covered_phases']:
                    # Candidate brings new phase capability
                    synergy_score += phase_points * 1.2  # Bonus for new phase coverage
                else:
                    # Check if candidate significantly improves existing phase coverage
                    current_max = alliance_coverage[challenge]['max_points'].get(phase_name, 0)
                    if phase_points > current_max:
                        synergy_score += (phase_points - current_max)  # Value of improvement
        
        return synergy_score
    
    # Build alliances with improved phase-specific synergy calculation
    for seed_team in top_teams[:10]:  # Use top 10 teams as seeds
        if seed_team not in available_teams:
            continue
        
        alliance = [seed_team]
        available_teams.remove(seed_team)
        
        # Find complementary teams based on phase-specific strengths
        while len(alliance) < tamanho_alianca and available_teams:
            best_synergy = -1
            best_team = None
            
            for candidate in available_teams:
                synergy = calculate_alliance_synergy(alliance, candidate)
                if synergy > best_synergy:
                    best_synergy = synergy
                    best_team = candidate
            
            if best_team:
                alliance.append(best_team)
                available_teams.remove(best_team)
            else:
                break
        
        # Calculate alliance metrics
        alliance_total_points = sum(team_rankings[team_rankings['team'].isin(alliance)]['total_points'])
        
        # Calculate phase coverage for visualization
        alliance_phase_coverage = phase_performance[
            phase_performance['team'].isin(alliance)
        ].groupby(['challenge_name', 'phase_name']).agg({
            'total_points': 'sum'
        }).reset_index()
        
        # Calculate balance score based on phase coverage
        phase_balance = alliance_phase_coverage['total_points'].std() / alliance_phase_coverage['total_points'].mean() if len(alliance_phase_coverage) > 0 else 1
        
        aliances.append({
            'teams': alliance,
            'total_points': alliance_total_points,
            'balance_score': 1 / (1 + phase_balance),  # Higher is better
            'phase_coverage': alliance_phase_coverage
        })
    
    # Sort alliances by a combination of total points and phase balance
    aliances.sort(key=lambda x: (x['total_points'] * x['balance_score']), reverse=True)
    
    print(f"Alliance builder ran in {time.time() - start_time:.2f} seconds")
    
    return aliances

# Add this helper function for CSV export
def convert_df_to_csv(df):
    """Converts a DataFrame to a CSV string for download."""
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    return csv_buffer.getvalue()

def main():
    st.title("🤖 FRC REEFSCAPE Dashboard")
    
    # Load and process data with progress indicators
    with st.spinner("Carregando dados..."):
        df = carregar_dados()
        df = processar_dados(df)
    
    # Calculate rankings with caching
    with st.spinner("Calculando rankings..."):
        team_rankings, challenge_rankings = calcular_rankings(df)
    
    # Create tabs but defer heavy computation
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Classificação", "🏆 Desafios", "🤖 Alianças", "🔍 Estatísticas de Robôs"])
    
    with tab1:
        st.header("Classificação das Equipes")
        
        # Table with teams as rows and metrics as columns
        st.subheader("Tabela de Classificação")
        
        # Rename columns for display and format the rankings table
        display_rankings = team_rankings.copy()
        display_rankings = display_rankings.rename(columns={
            'team': 'Equipe',
            'total_points': 'Pontos Totais',
            'auto_points': 'Pontos Autônomo',
            'teleop_points': 'Pontos Teleoperado',
            'rank': 'Classificação'
        })
        
        # Sort by rank
        display_rankings = display_rankings.sort_values('Classificação')
        
        # Convert numeric columns to integers for cleaner display
        for col in ['Pontos Totais', 'Pontos Autônomo', 'Pontos Teleoperado', 'Classificação']:
            display_rankings[col] = display_rankings[col].astype(int)
        
        # Display the table
        st.dataframe(
            display_rankings[['Classificação', 'Equipe', 'Pontos Totais', 'Pontos Autônomo', 'Pontos Teleoperado']],
            use_container_width=True
        )
        
        # Add export button
        st.download_button(
            label="📥 Exportar Classificação (CSV)",
            data=convert_df_to_csv(display_rankings),
            file_name="frc_rankings.csv",
            mime="text/csv",
        )
        
        # Top 10 Equipes
        st.subheader("Top 10 Equipes")
        # Horizontal bar chart for better column-like visualization
        fig = px.bar(
            team_rankings.sort_values('total_points', ascending=True).tail(10),
            y='team',
            x=['auto_points', 'teleop_points'],
            title="Top 10 Equipes por Pontuação",
            labels={'team': 'Equipe', 'value': 'Pontos', 'variable': 'Tipo'},
            barmode='stack',
            orientation='h'
        )
        
        # Update colors and layout
        fig.update_layout(legend_title_text='Modo')
        fig.update_traces(marker_line_width=0)
        fig.update_yaxes(categoryorder='total ascending')
        
        st.plotly_chart(fig, use_container_width=True)
    
    with tab2:
        st.header("Análise por Desafio")
        
        # Get unique challenges
        challenges = sorted(df['challenge_name'].unique())
        selected_challenge = st.selectbox("Selecione um desafio:", challenges)
        
        # Display challenge analysis in vertical layout
        if selected_challenge:
            # Filter for selected challenge
            challenge_df = df[df['challenge_name'] == selected_challenge]
            
            # Calculate rankings for this challenge
            challenge_team_rankings = challenge_df.groupby('team').agg({
                'auto_points': 'sum',
                'teleop_points': 'sum',
                'total_points': 'sum'
            }).reset_index()
            
            challenge_team_rankings['rank'] = challenge_team_rankings['total_points'].rank(
                ascending=False, method='min').astype(int)
            challenge_team_rankings = challenge_team_rankings.sort_values('rank')
            
            # Display classification table for this challenge
            st.subheader(f"Classificação das Equipes no Desafio {selected_challenge}")
            
            # Prepare the display table with proper column names
            display_challenge_rankings = challenge_team_rankings.copy()
            display_challenge_rankings = display_challenge_rankings.rename(columns={
                'team': 'Equipe',
                'total_points': 'Pontos Totais',
                'auto_points': 'Pontos Autônomo',
                'teleop_points': 'Pontos Teleoperado',
                'rank': 'Classificação'
            })
            
            # Convert numeric columns to integers for cleaner display
            for col in ['Pontos Totais', 'Pontos Autônomo', 'Pontos Teleoperado', 'Classificação']:
                display_challenge_rankings[col] = display_challenge_rankings[col].astype(int)
            
            # Display the table
            st.dataframe(
                display_challenge_rankings[['Classificação', 'Equipe', 'Pontos Totais', 'Pontos Autônomo', 'Pontos Teleoperado']],
                use_container_width=True
            )
            
            # Add export button for this challenge ranking
            st.download_button(
                label=f"📥 Exportar Classificação - {selected_challenge} (CSV)",
                data=convert_df_to_csv(display_challenge_rankings),
                file_name=f"frc_ranking_{selected_challenge.replace(' ', '_')}.csv",
                mime="text/csv",
            )
            
            # Pontuação por Equipe - now stacked vertically
            st.subheader("Pontuação por Equipe")
            
            # Create column-based metrics display
            metrics_cols = st.columns(min(5, len(challenge_team_rankings)))
            for i, (_, row) in enumerate(challenge_team_rankings.iterrows()):
                col_index = i % len(metrics_cols)
                with metrics_cols[col_index]:
                    st.metric(
                        f"{row['team']}",
                        f"{int(row['total_points'])} pts",
                        f"Rank: {int(row['rank'])}"
                    )

            challenge_data = df[df['challenge_name'] == selected_challenge]
            
            # Prepare data for visualization
            phase_team_data = challenge_data.groupby(['team', 'phase_name']).agg({
                'total_points': 'sum'
            }).reset_index()
            
            
            
            # Add a spider/radar chart as an alternative view for top teams
            st.subheader("Comparativo das Melhores Equipes por Fase")
            
            # Get top 5 teams for this challenge
            top_teams = challenge_team_rankings.sort_values('total_points', ascending=False).head(5)['team'].tolist()
            
            # Filter data for top teams
            top_team_data = phase_team_data[phase_team_data['team'].isin(top_teams)]
            
            # Create radar chart
            radar_fig = go.Figure()
            
            for team in top_teams:
                team_phases = top_team_data[top_team_data['team'] == team]
                
                if not team_phases.empty:
                    radar_fig.add_trace(go.Scatterpolar(
                        r=team_phases['total_points'],
                        theta=team_phases['phase_name'],
                        fill='toself',
                        name=team
                    ))
            
            radar_fig.update_layout(
                polar=dict(
                    radialaxis=dict(
                        visible=True,
                    )
                ),
                title="Comparativo das Top Equipes por Fase",
                showlegend=True
            )
            
            st.plotly_chart(radar_fig, use_container_width=True)
            
            # Add export button for challenge data
            challenge_export = challenge_df.groupby(['team', 'phase_name']).agg({
                'total_points': 'sum',
                'auto_points': 'sum',
                'teleop_points': 'sum'
            }).reset_index()
            
            st.download_button(
                label=f"📥 Exportar Dados do Desafio {selected_challenge} (CSV)",
                data=convert_df_to_csv(challenge_export),
                file_name=f"frc_challenge_{selected_challenge.replace(' ', '_')}.csv",
                mime="text/csv",
            )
    
    with tab3:
        st.header("Alianças Sugeridas")
        
        # Alliance configuration - removed slider and fixed alliance size to 3
        st.subheader("Configurar Alianças")
        alliance_size = 3  # Fixed alliance size
        
        # Set MINERSKILLS as default selected team with flexible matching
        team_options = [""] + list(team_rankings['team'])
        
        # Find any team containing "MINERSKILLS" or "10019"
        default_index = 0  # Default to empty string if team not found
        for i, team in enumerate(team_options):
            if "MINERS" in team.upper() or "10019" in team:
                default_index = i
                break
        
        selected_team = st.selectbox(
            "Selecione uma equipe:",
            options=team_options,
            index=default_index
        )
        
        # Only do expensive calculations if a team is selected
        if selected_team:
            with st.spinner("Calculando alianças otimizadas..."):
                # Get team's challenge performance
                team_challenge_points = challenge_rankings[challenge_rankings['team'] == selected_team]
                
                # Simpler version - just get best/worst challenges without phase detail
                best_challenges = team_challenge_points.sort_values('total_points', ascending=False).head(2)
                worst_challenges = team_challenge_points.sort_values('total_points').head(2)
                
                st.write("### Perfil de Desempenho")
                cols = st.columns(2)
                with cols[0]:
                    st.write("**Pontos Fortes:**")
                    for _, row in best_challenges.iterrows():
                        st.write(f"- {row['challenge_name']}: {int(row['total_points'])} pts")
                
                with cols[1]:
                    st.write("**Pontos Fracos:**")
                    for _, row in worst_challenges.iterrows():
                        st.write(f"- {row['challenge_name']}: {int(row['total_points'])} pts")
                
                # Simplified alliance building logic
                # Start with the selected team
                alliance = [selected_team]
                
                # For each weak challenge, find a strong team
                for _, challenge_row in worst_challenges.iterrows():
                    if len(alliance) >= alliance_size:
                        break
                        
                    challenge = challenge_row['challenge_name']
                    
                    # Find strong teams in this challenge
                    strong_teams = challenge_rankings[
                        (challenge_rankings['challenge_name'] == challenge) & 
                        ~(challenge_rankings['team'].isin(alliance))
                    ].sort_values('total_points', ascending=False).head(5)  # Limit to top 5
                    
                    if not strong_teams.empty:
                        best_team = strong_teams.iloc[0]['team']
                        alliance.append(best_team)
                
                # If alliance still not complete, add highest scoring available teams
                while len(alliance) < alliance_size:
                    remaining = team_rankings[
                        ~team_rankings['team'].isin(alliance)
                    ].sort_values('total_points', ascending=False).head(5)  # Limit to top 5
                    
                    if remaining.empty:
                        break
                        
                    alliance.append(remaining.iloc[0]['team'])
                
                # Calculate alliance total points
                alliance_points = team_rankings[team_rankings['team'].isin(alliance)]['total_points'].sum()
                
                st.subheader(f"Aliança Complementar com {selected_team}")
                
                # Show teams in horizontal columns
                team_cols = st.columns(len(alliance))
                for j, team in enumerate(alliance):
                    with team_cols[j]:
                        team_data = team_rankings[team_rankings['team'] == team].iloc[0]
                        
                        # Display team name and rank
                        st.metric(
                            f"Equipe {j+1}", 
                            team, 
                            f"Rank: {int(team_data['rank'])}"
                        )
                        
                        # Get team's best challenge and phases
                        team_phases = df[df['team'] == team].groupby(
                            ['challenge_name', 'phase_name']
                        ).agg({
                            'total_points': 'sum'
                        }).reset_index()
                        
                        if not team_phases.empty:
                            # Group by challenge first to find best challenge
                            challenge_totals = team_phases.groupby('challenge_name')['total_points'].sum().reset_index()
                            best_challenge = challenge_totals.loc[challenge_totals['total_points'].idxmax()]
                            
                            # Find best phase within best challenge
                            best_phase = team_phases[
                                team_phases['challenge_name'] == best_challenge['challenge_name']
                            ].sort_values('total_points', ascending=False).iloc[0]
                            
                            st.markdown(f"""
                            **Melhor Desafio:** {best_challenge['challenge_name']}
                            - *Melhor Fase:* {best_phase['phase_name']}
                            - *Pontos:* {int(best_phase['total_points'])}
                            """)
                
                # Show total alliance points
                st.metric("Pontuação Total da Aliança", f"{int(alliance_points)} pontos")
                
                # Simplified challenge coverage visualization
                st.subheader("Cobertura de Desafios da Aliança")
                alliance_by_challenge = challenge_rankings[
                    challenge_rankings['team'].isin(alliance)
                ].groupby('challenge_name').agg({
                    'total_points': 'sum'
                }).reset_index()
                
                # Use simpler bar chart instead of radar chart
                fig = px.bar(
                    alliance_by_challenge.sort_values('total_points', ascending=False),
                    x='challenge_name',
                    y='total_points',
                    title="Pontuação por Desafio",
                    labels={'challenge_name': 'Desafio', 'total_points': 'Pontos Totais'}
                )
                st.plotly_chart(fig, use_container_width=True)
        
        else:
            # Show a maximum of 3 pre-computed alliances to avoid performance issues
            with st.spinner("Calculando melhores alianças..."):
                alliances = construir_alianca_otima(team_rankings, challenge_rankings, df, alliance_size, max_teams=20)
                
                st.subheader(f"Melhores Alianças Complementares (Tamanho: {alliance_size})")
                
                # Only show top 3 alliances
                for i, alliance in enumerate(alliances[:3]):
                    st.markdown(f"### Aliança {i+1} - {int(alliance['total_points'])} pontos")
                    
                    # Show teams in horizontal columns
                    team_cols = st.columns(len(alliance['teams']))
                    for j, team in enumerate(alliance['teams']):
                        with team_cols[j]:
                            team_data = team_rankings[team_rankings['team'] == team].iloc[0]
                            
                            # Display team name and rank
                            st.metric(
                                f"Equipe {j+1}", 
                                team, 
                                f"Rank: {int(team_data['rank'])}"
                            )
                            
                            # Get team's best challenge and phases
                            team_phases = df[df['team'] == team].groupby(
                                ['challenge_name', 'phase_name']
                            ).agg({
                                'total_points': 'sum'
                            }).reset_index()
                            
                            if not team_phases.empty:
                                # Group by challenge first to find best challenge
                                challenge_totals = team_phases.groupby('challenge_name')['total_points'].sum().reset_index()
                                best_challenge = challenge_totals.loc[challenge_totals['total_points'].idxmax()]
                                
                                # Find best phase within best challenge
                                best_phase = team_phases[
                                    team_phases['challenge_name'] == best_challenge['challenge_name']
                                ].sort_values('total_points', ascending=False).iloc[0]
                                
                                st.markdown(f"""
                                **Melhor Desafio:** {best_challenge['challenge_name']}
                                - *Melhor Fase:* {best_phase['phase_name']}
                                - *Pontos:* {int(best_phase['total_points'])}
                                """)
                    
                    # Show phase coverage visualization
                    if 'phase_coverage' in alliance and not alliance['phase_coverage'].empty:
                        st.subheader("Cobertura de Fases da Aliança")
                        
                        # Create a more detailed visualization showing phases within challenges
                        coverage_data = alliance['phase_coverage'].sort_values(['challenge_name', 'total_points'], ascending=[True, False])
                        
                        fig = px.bar(
                            coverage_data,
                            x='phase_name',
                            y='total_points',
                            color='challenge_name',
                            title="Pontuação por Fase em cada Desafio",
                            labels={
                                'phase_name': 'Fase',
                                'total_points': 'Pontos Totais',
                                'challenge_name': 'Desafio'
                            },
                            barmode='group'
                        )
                        
                        fig.update_layout(
                            xaxis_title="Fases",
                            yaxis_title="Pontos",
                            legend_title="Desafios"
                        )
                        
                        st.plotly_chart(fig, use_container_width=True)
                    
                    st.markdown("---")  # Add a separator between alliances

    with tab4:
        st.header("Estatísticas de Robôs")
        
        # Get unique robots
        robots = sorted(df['team'].unique())
        
        # Create a selectbox to choose a robot
        selected_robot = st.selectbox("Selecione um robô:", robots, key="robot_stats_select")
        
        if selected_robot:
            # Filter data for the selected robot
            robot_data = df[df['team'] == selected_robot]
            
            # Get robot's overall ranking
            robot_rank = team_rankings[team_rankings['team'] == selected_robot]['rank'].values[0]
            robot_total_points = team_rankings[team_rankings['team'] == selected_robot]['total_points'].values[0]
            
            # Display robot info in a card-like format
            st.subheader(f"Robô: {selected_robot}")
            
            # Create columns for metrics
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Classificação Geral", f"{int(robot_rank)}º")
            with col2:
                st.metric("Pontuação Total", f"{int(robot_total_points)} pts")
            with col3:
                # Get robot's alliance
                alliance = robot_data['alliance'].iloc[0] if not robot_data.empty else "N/A"
                st.metric("Aliança", alliance.upper() if isinstance(alliance, str) else "N/A")
            
            # Calculate performance by challenge
            challenge_performance = robot_data.groupby('challenge_name').agg({
                'auto_points': 'sum',
                'teleop_points': 'sum',
                'total_points': 'sum'
            }).reset_index()
            
            # Find best challenge
            if not challenge_performance.empty:
                best_challenge = challenge_performance.loc[challenge_performance['total_points'].idxmax()]
                
                st.subheader("Melhor Desafio")
                st.info(f"**{best_challenge['challenge_name']}** com **{int(best_challenge['total_points'])}** pontos")
                
                # Show detailed phase performance
                st.subheader("Desempenho por Fase")
                
                # Calculate performance by phase
                phase_performance = robot_data.groupby(['challenge_name', 'phase_name']).agg({
                    'auto_points': 'sum',
                    'teleop_points': 'sum',
                    'total_points': 'sum',
                    'completed_autonomous': 'sum',
                    'completed_teleop': 'sum'
                }).reset_index()
                
                # Create a more detailed table
                phase_display = phase_performance.copy()
                phase_display = phase_display.rename(columns={
                    'challenge_name': 'Desafio',
                    'phase_name': 'Fase',
                    'auto_points': 'Pontos Autônomo',
                    'teleop_points': 'Pontos Teleoperado',
                    'total_points': 'Pontos Totais',
                    'completed_autonomous': 'Completados Autônomo',
                    'completed_teleop': 'Completados Teleoperado'
                })
                
                # Convert numeric columns to integers for cleaner display
                for col in ['Pontos Totais', 'Pontos Autônomo', 'Pontos Teleoperado', 
                           'Completados Autônomo', 'Completados Teleoperado']:
                    phase_display[col] = phase_display[col].astype(int)
                
                # Display the table
                st.dataframe(
                    phase_display.sort_values(['Desafio', 'Pontos Totais'], ascending=[True, False]),
                    use_container_width=True
                )
                
                # Add export button for robot data
                st.download_button(
                    label=f"📥 Exportar Estatísticas de {selected_robot} (CSV)",
                    data=convert_df_to_csv(phase_display),
                    file_name=f"robot_stats_{selected_robot.replace(' ', '_').replace('#', '')}.csv",
                    mime="text/csv",
                )
            else:
                st.warning(f"Não há dados de desempenho disponíveis para {selected_robot}")
        
        # Add a section to compare robots
        st.subheader("Comparar Robôs")
        
        # Multi-select for robots
        selected_robots = st.multiselect(
            "Selecione robôs para comparar:",
            options=robots,
            default=[]
        )
        
        if selected_robots:
            # Get data for selected robots
            compare_data = df[df['team'].isin(selected_robots)]
            
            # Calculate total points by robot and challenge
            robot_challenge_points = compare_data.groupby(['team', 'challenge_name']).agg({
                'total_points': 'sum'
            }).reset_index()
            
            # Create comparison chart
            st.subheader("Comparação por Desafio")
            
            fig = px.bar(
                robot_challenge_points,
                x='challenge_name',
                y='total_points',
                color='team',
                barmode='group',
                title="Comparação de Pontuação por Desafio",
                labels={
                    'challenge_name': 'Desafio',
                    'total_points': 'Pontos Totais',
                    'team': 'Robô'
                }
            )
            
            st.plotly_chart(fig, use_container_width=True)
            
            # Create a radar chart for a different visualization
            st.subheader("Gráfico Radar de Comparação")
            
            # Pivot the data for radar chart
            radar_data = robot_challenge_points.pivot(index='team', columns='challenge_name', values='total_points').fillna(0)
            
            # Create radar chart
            radar_fig = go.Figure()
            
            for robot in radar_data.index:
                radar_fig.add_trace(go.Scatterpolar(
                    r=radar_data.loc[robot].values,
                    theta=radar_data.columns,
                    fill='toself',
                    name=robot
                ))
            
            radar_fig.update_layout(
                polar=dict(
                    radialaxis=dict(
                        visible=True,
                    )
                ),
                title="Comparação de Robôs por Desafio",
                showlegend=True
            )
            
            st.plotly_chart(radar_fig, use_container_width=True)
            
            # Add a summary table
            st.subheader("Tabela Comparativa")
            
            # Get overall stats for selected robots
            compare_summary = team_rankings[team_rankings['team'].isin(selected_robots)].copy()
            compare_summary = compare_summary.rename(columns={
                'team': 'Robô',
                'total_points': 'Pontos Totais',
                'auto_points': 'Pontos Autônomo',
                'teleop_points': 'Pontos Teleoperado',
                'rank': 'Classificação'
            })
            
            # Convert numeric columns to integers for cleaner display
            for col in ['Pontos Totais', 'Pontos Autônomo', 'Pontos Teleoperado', 'Classificação']:
                compare_summary[col] = compare_summary[col].astype(int)
            
            # Display the table
            st.dataframe(
                compare_summary.sort_values('Classificação')[['Robô', 'Classificação', 'Pontos Totais', 'Pontos Autônomo', 'Pontos Teleoperado']],
                use_container_width=True
            )
            
            # Add export button for comparison data
            st.download_button(
                label="📥 Exportar Comparação (CSV)",
                data=convert_df_to_csv(compare_summary),
                file_name="robot_comparison.csv",
                mime="text/csv",
            )

if __name__ == "__main__":
    main()