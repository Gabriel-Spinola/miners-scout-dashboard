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
    """Optimized alliance builder that limits processing to top teams"""
    # Start timer for performance monitoring
    start_time = time.time()
    
    # Limit to top teams for better performance
    top_teams = team_rankings.sort_values('total_points', ascending=False).head(max_teams)['team'].tolist()
    available_teams = set(top_teams)
    
    aliances = []
    
    # Create simplified phase-level performance data - only for top teams
    phase_rankings = df_processed[df_processed['team'].isin(top_teams)].groupby(
        ['team', 'challenge_name', 'phase_name']
    ).agg({
        'total_points': 'sum'
    }).reset_index()
    
    # Limit the number of alliance combinations we evaluate
    for _, team_row in top_teams[:10]:  # Only use top 10 teams as seeds
        team = team_row
        
        # Skip if team already in an alliance
        if team not in available_teams:
            continue
            
        # Start building alliance with this team
        alliance = [team]
        available_teams.remove(team)
        
        # Find team's challenge strengths and weaknesses - use pre-filtered data
        team_challenge_performance = challenge_rankings[
            (challenge_rankings['team'] == team) & 
            (challenge_rankings['team'].isin(top_teams))
        ].copy()
        
        if team_challenge_performance.empty:
            continue
        
        # Find team's phase-level strengths and weaknesses - use pre-filtered data
        team_phase_performance = phase_rankings[phase_rankings['team'] == team].copy()
        
        # Find weakest challenges for this team - limit to top 2 weaknesses only
        weakest_challenges = team_challenge_performance.sort_values('total_points').head(2)['challenge_name'].values
        
        # For each weak challenge, find a complementary team - simplified approach
        for challenge in weakest_challenges:
            if len(alliance) >= tamanho_alianca:
                break
                
            # Find teams that are strong in this challenge - use pre-filtered list
            strong_teams_in_challenge = challenge_rankings[
                (challenge_rankings['challenge_name'] == challenge) & 
                (challenge_rankings['team'].isin(available_teams))
            ].sort_values('total_points', ascending=False).head(3)  # Limit to top 3 matches
            
            if not strong_teams_in_challenge.empty:
                best_team = strong_teams_in_challenge.iloc[0]['team']
                alliance.append(best_team)
                available_teams.remove(best_team)
        
        # If alliance still not complete, add best available based on overall points
        while len(alliance) < tamanho_alianca and available_teams:
            best_available = team_rankings[
                team_rankings['team'].isin(available_teams)
            ].sort_values('total_points', ascending=False).iloc[0]['team']
            
            alliance.append(best_available)
            available_teams.remove(best_available)
        
        # Calculate alliance total points
        alliance_points = team_rankings[team_rankings['team'].isin(alliance)]['total_points'].sum()
        
        # Get challenge distribution - simplified
        alliance_challenge_points = challenge_rankings[
            challenge_rankings['team'].isin(alliance)
        ].groupby('challenge_name').agg({
            'total_points': 'sum'
        }).reset_index()
        
        # Calculate a simplified balance score
        balance_score = 0
        if len(alliance_challenge_points) > 0:
            balance_score = alliance_challenge_points['total_points'].mean()
            
        aliances.append({
            'teams': alliance,
            'total_points': alliance_points,
            'balance_score': balance_score,
            'challenge_coverage': alliance_challenge_points
        })
    
    # Sort alliances by total points (simpler sort criterion)
    aliances.sort(key=lambda x: x['total_points'], reverse=True)
    
    # Log performance
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
    tab1, tab2, tab3 = st.tabs(["📊 Classificação", "🏆 Desafios", "🤖 Alianças"])
    
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
            
            # Show performance by phase - now stacked vertically
            st.subheader("Desempenho das Equipes por Fase")
            
            # Get data for the selected challenge
            challenge_data = df[df['challenge_name'] == selected_challenge]
            
            # Prepare data for visualization
            phase_team_data = challenge_data.groupby(['team', 'phase_name']).agg({
                'total_points': 'sum'
            }).reset_index()
            
            # Create a more readable horizontal bar chart grouped by phase
            fig = px.bar(
                phase_team_data,
                x='total_points',
                y='team',
                color='phase_name',
                orientation='h',
                barmode='group',
                height=max(400, len(phase_team_data['team'].unique()) * 30),  # Dynamic height based on number of teams
                labels={
                    'total_points': 'Pontuação',
                    'team': 'Equipe',
                    'phase_name': 'Fase'
                },
                title=f"Desempenho no Desafio: {selected_challenge}"
            )
            
            # Improve readability
            fig.update_layout(
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1
                ),
                margin=dict(l=20, r=20, t=60, b=20)
            )
            
            st.plotly_chart(fig, use_container_width=True)
            
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
                        
                        # Get team's best challenge - simplified
                        best_challenge = challenge_rankings[
                            challenge_rankings['team'] == team
                        ].sort_values('total_points', ascending=False)
                        
                        if not best_challenge.empty:
                            best_challenge_name = best_challenge.iloc[0]['challenge_name']
                        else:
                            best_challenge_name = "N/A"
                        
                        st.metric(
                            f"Equipe {j+1}", 
                            team, 
                            f"Rank: {int(team_data['rank'])}"
                        )
                        
                        # Simpler display
                        st.markdown(f"**Melhor Desafio:** {best_challenge_name}")
                
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
                            
                            # Simpler display
                            st.metric(
                                f"Equipe {j+1}", 
                                team, 
                                f"Rank: {int(team_data['rank'])}"
                            )
                            
                            # Get just best challenge, skip phase details
                            best_challenge = challenge_rankings[
                                challenge_rankings['team'] == team
                            ].sort_values('total_points', ascending=False)
                            
                            if not best_challenge.empty:
                                best_challenge_name = best_challenge.iloc[0]['challenge_name']
                                st.markdown(f"**Melhor Desafio:** {best_challenge_name}")
                    
                    # Simpler alliance visualization - just a bar chart
                    if 'challenge_coverage' in alliance and not alliance['challenge_coverage'].empty:
                        coverage_data = alliance['challenge_coverage'].sort_values('total_points', ascending=False)
                        
                        fig = px.bar(
                            coverage_data,
                            x='challenge_name',
                            y='total_points',
                            title="Pontuação por Desafio",
                            labels={'challenge_name': 'Desafio', 'total_points': 'Pontos Totais'}
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    
                    st.markdown("---")  # Add a separator between alliances

if __name__ == "__main__":
    main()