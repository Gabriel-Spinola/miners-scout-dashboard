import streamlit as st
import pandas as pd
import mysql.connector
import plotly.express as px
import plotly.graph_objects as go
from dotenv import load_dotenv
import os
import numpy as np

# Page configuration
st.set_page_config(
    page_title="FRC REEFSCAPE Dashboard",
    page_icon="🤖",
    layout="wide"
)

# Load environment variables
load_dotenv()

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

@st.cache_data(ttl=300)
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
    
    # Loop through to calculate points
    for i, row in df.iterrows():
        phase = row['phase_key']
        if phase in POINTS_MAP:
            df.at[i, 'auto_points'] = row['completed_autonomous'] * POINTS_MAP[phase]['auto']
            df.at[i, 'teleop_points'] = row['completed_teleop'] * POINTS_MAP[phase]['teleop']
    
    # Calculate total points
    df['total_points'] = df['auto_points'] + df['teleop_points']
    
    return df

def construir_alianca_otima(team_rankings, challenge_rankings, df_processed, tamanho_alianca=3):
    """
    Build optimal alliances by finding teams with complementary challenge and phase abilities
    
    Parameters:
    - team_rankings: Overall team rankings
    - challenge_rankings: Team rankings by challenge
    - df_processed: Processed dataframe with phase-level details
    - tamanho_alianca: Size of each alliance
    
    Returns:
    - List of alliances, each with teams and total points
    """
    aliances = []
    available_teams = set(team_rankings['team'])
    
    # Create phase-level performance data
    phase_rankings = df_processed.groupby(['team', 'challenge_name', 'phase_name']).agg({
        'total_points': 'sum'
    }).reset_index()
    
    # For each team, find optimal partners based on challenge and phase complementarity
    for _, team_row in team_rankings.iterrows():
        team = team_row['team']
        
        # Skip if team already in an alliance
        if team not in available_teams:
            continue
            
        # Start building alliance with this team
        alliance = [team]
        available_teams.remove(team)
        
        # Find team's challenge strengths and weaknesses
        team_challenge_performance = challenge_rankings[challenge_rankings['team'] == team].copy()
        if team_challenge_performance.empty:
            continue
        
        # Find team's phase-level strengths and weaknesses
        team_phase_performance = phase_rankings[phase_rankings['team'] == team].copy()
        
        # Find weakest challenges for this team
        weakest_challenges = team_challenge_performance.sort_values('total_points').head(2)['challenge_name'].values
        
        # For each weak challenge, find a complementary team
        for challenge in weakest_challenges:
            if len(alliance) >= tamanho_alianca:
                break
                
            # Find teams that are strong in this challenge and still available
            strong_teams_in_challenge = challenge_rankings[
                (challenge_rankings['challenge_name'] == challenge) & 
                (challenge_rankings['team'].isin(available_teams))
            ].sort_values('total_points', ascending=False)
            
            if not strong_teams_in_challenge.empty:
                best_team = strong_teams_in_challenge.iloc[0]['team']
                alliance.append(best_team)
                available_teams.remove(best_team)
        
        # If alliance still not complete, look at phase-level complementarity
        if len(alliance) < tamanho_alianca:
            # Find weakest phases for this team
            weakest_phases = team_phase_performance.sort_values('total_points').head(3)
            
            for _, phase_row in weakest_phases.iterrows():
                if len(alliance) >= tamanho_alianca:
                    break
                    
                challenge = phase_row['challenge_name']
                phase = phase_row['phase_name']
                
                # Find teams that are strong in this specific phase
                strong_teams_in_phase = phase_rankings[
                    (phase_rankings['challenge_name'] == challenge) &
                    (phase_rankings['phase_name'] == phase) &
                    (phase_rankings['team'].isin(available_teams))
                ].sort_values('total_points', ascending=False)
                
                if not strong_teams_in_phase.empty:
                    best_team = strong_teams_in_phase.iloc[0]['team']
                    # Only add if not already in alliance
                    if best_team not in alliance:
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
        
        # Get challenge distribution for this alliance
        alliance_challenge_points = challenge_rankings[
            challenge_rankings['team'].isin(alliance)
        ].groupby('challenge_name').agg({
            'total_points': 'sum'
        }).reset_index()
        
        # Get phase distribution for this alliance
        alliance_phase_points = phase_rankings[
            phase_rankings['team'].isin(alliance)
        ].groupby(['challenge_name', 'phase_name']).agg({
            'total_points': 'sum'
        }).reset_index()
        
        # Calculate alliance quality scores
        if len(alliance_challenge_points) > 0:
            challenge_std = alliance_challenge_points['total_points'].std()
            challenge_mean = alliance_challenge_points['total_points'].mean()
            challenge_balance = challenge_mean / (challenge_std + 1)  # +1 to avoid division by zero
        else:
            challenge_balance = 0
            
        if len(alliance_phase_points) > 0:
            phase_std = alliance_phase_points['total_points'].std()
            phase_mean = alliance_phase_points['total_points'].mean()
            phase_balance = phase_mean / (phase_std + 1)
        else:
            phase_balance = 0
        
        # Combined balance score - challenge balance has more weight (70%) than phase balance (30%)
        balance_score = (challenge_balance * 0.7) + (phase_balance * 0.3)
            
        aliances.append({
            'teams': alliance,
            'total_points': alliance_points,
            'balance_score': balance_score,
            'challenge_coverage': alliance_challenge_points,
            'phase_coverage': alliance_phase_points
        })
    
    # Sort alliances by both total points and balance - prioritize well-rounded alliances
    aliances.sort(key=lambda x: (x['balance_score'] * 0.4 + x['total_points'] * 0.6), reverse=True)
    
    return aliances

def main():
    st.title("🤖 FRC REEFSCAPE Dashboard")
    
    # Load and process data
    with st.spinner("Carregando dados..."):
        df = carregar_dados()
        df = processar_dados(df)
    
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
    
    # Create tabs for main sections
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
        
        # Debug: print available teams to the console to see what's available
        print("Available teams:", team_options)
        
        selected_team = st.selectbox(
            "Selecione uma equipe:",
            options=team_options,
            index=default_index
        )
        
        # Build alliances with challenge and phase complementarity
        if selected_team:
            # Get team's challenge performance
            team_challenge_points = challenge_rankings[challenge_rankings['team'] == selected_team]
            
            # Get team's phase performance
            team_phase_points = df.loc[df['team'] == selected_team].groupby(['challenge_name', 'phase_name']).agg({
                'total_points': 'sum'
            }).reset_index()
            
            # Identify best and worst challenges
            best_challenges = team_challenge_points.sort_values('total_points', ascending=False).head(2)
            worst_challenges = team_challenge_points.sort_values('total_points').head(2)
            
            # Identify best and worst phases
            best_phases = team_phase_points.sort_values('total_points', ascending=False).head(3)
            worst_phases = team_phase_points.sort_values('total_points').head(3)
            
            st.write("### Perfil de Desempenho")
            
            # Show challenge strengths/weaknesses
            challenge_cols = st.columns(2)
            with challenge_cols[0]:
                st.write("**Desafios Fortes:**")
                for _, row in best_challenges.iterrows():
                    st.write(f"- {row['challenge_name']}: {int(row['total_points'])} pts")
            
            with challenge_cols[1]:
                st.write("**Desafios Fracos:**")
                for _, row in worst_challenges.iterrows():
                    st.write(f"- {row['challenge_name']}: {int(row['total_points'])} pts")
            
            # Show phase strengths/weaknesses
            phase_cols = st.columns(2)
            with phase_cols[0]:
                st.write("**Fases Fortes:**")
                for _, row in best_phases.iterrows():
                    st.write(f"- {row['challenge_name']} ({row['phase_name']}): {int(row['total_points'])} pts")
            
            with phase_cols[1]:
                st.write("**Fases Fracas:**")
                for _, row in worst_phases.iterrows():
                    st.write(f"- {row['challenge_name']} ({row['phase_name']}): {int(row['total_points'])} pts")
            
            # Find complementary teams based on the updated alliance building logic
            available_teams = team_rankings[team_rankings['team'] != selected_team]
            
            # Start with the selected team
            alliance = [selected_team]
            
            # For each weak challenge, find a complementary team
            for _, challenge_row in worst_challenges.iterrows():
                if len(alliance) >= alliance_size:
                    break
                    
                challenge = challenge_row['challenge_name']
                
                # Find teams that are strong in this challenge
                strong_teams = challenge_rankings[
                    (challenge_rankings['challenge_name'] == challenge) & 
                    ~(challenge_rankings['team'].isin(alliance))
                ].sort_values('total_points', ascending=False)
                
                if not strong_teams.empty:
                    best_team = strong_teams.iloc[0]['team']
                    alliance.append(best_team)
            
            # If still need teams, look at worst phases
            if len(alliance) < alliance_size:
                for _, phase_row in worst_phases.iterrows():
                    if len(alliance) >= alliance_size:
                        break
                        
                    challenge = phase_row['challenge_name']
                    phase = phase_row['phase_name']
                    
                    # Find teams good at this phase
                    phase_data = df.loc[
                        (df['challenge_name'] == challenge) & 
                        (df['phase_name'] == phase) & 
                        ~(df['team'].isin(alliance))
                    ]
                    
                    if not phase_data.empty:
                        phase_team_points = phase_data.groupby('team').agg({
                            'total_points': 'sum'
                        }).reset_index().sort_values('total_points', ascending=False)
                        
                        if not phase_team_points.empty:
                            best_team = phase_team_points.iloc[0]['team']
                            alliance.append(best_team)
            
            # If alliance still not complete, add highest scoring available teams
            while len(alliance) < alliance_size:
                remaining = team_rankings[
                    ~team_rankings['team'].isin(alliance)
                ].sort_values('total_points', ascending=False)
                
                if remaining.empty:
                    break
                    
                alliance.append(remaining.iloc[0]['team'])
            
            # Calculate alliance total points
            alliance_points = team_rankings[team_rankings['team'].isin(alliance)]['total_points'].sum()
            
            st.subheader(f"Aliança Complementar com {selected_team}")
            
            # Show alliance teams with their specialties
            cols = st.columns(len(alliance))
            for i, team in enumerate(alliance):
                with cols[i]:
                    team_data = team_rankings[team_rankings['team'] == team].iloc[0]
                    st.metric(
                        f"Equipe {i+1}", 
                        team, 
                        f"Rank: {int(team_data['rank'])}"
                    )
                    
                    # Show team's best challenge
                    best_challenge = challenge_rankings[
                        challenge_rankings['team'] == team
                    ].sort_values('total_points', ascending=False).iloc[0]
                    
                    # Show team's best phase
                    best_phase = df.loc[df['team'] == team].groupby(['challenge_name', 'phase_name']).agg({
                        'total_points': 'sum'
                    }).reset_index().sort_values('total_points', ascending=False).iloc[0]
                    
                    st.markdown(f"""
                    **Pontos:** {int(team_data['total_points'])}  
                    **Melhor Desafio:** {best_challenge['challenge_name']}  
                    **Melhor Fase:** {best_phase['challenge_name']} ({best_phase['phase_name']})
                    """)
            
            # Show alliance challenge coverage
            st.subheader("Cobertura de Desafios da Aliança")
            
            # Calculate alliance performance in each challenge
            alliance_by_challenge = challenge_rankings[
                challenge_rankings['team'].isin(alliance)
            ].groupby('challenge_name').agg({
                'total_points': 'sum'
            }).reset_index()
            
            # Create a radar chart
            fig = go.Figure()
            
            fig.add_trace(go.Scatterpolar(
                r=alliance_by_challenge['total_points'],
                theta=alliance_by_challenge['challenge_name'],
                fill='toself',
                name='Pontos da Aliança'
            ))
            
            fig.update_layout(
                polar=dict(
                    radialaxis=dict(
                        visible=True,
                    )
                ),
                title="Cobertura de Desafios da Aliança"
            )
            
            st.plotly_chart(fig, use_container_width=True)
            
            # Show alliance phase coverage for the weakest challenge
            if not worst_challenges.empty:
                weakest_challenge = worst_challenges.iloc[0]['challenge_name']
                
                st.subheader(f"Cobertura de Fases no Desafio {weakest_challenge}")
                
                # Get phases for this challenge
                alliance_phases = df[
                    (df['challenge_name'] == weakest_challenge) & 
                    (df['team'].isin(alliance))
                ].groupby('phase_name').agg({
                    'total_points': 'sum'
                }).reset_index()
                
                if not alliance_phases.empty:
                    fig = px.bar(
                        alliance_phases,
                        x='phase_name',
                        y='total_points',
                        title=f"Pontuação por Fase no Desafio {weakest_challenge}",
                        labels={'phase_name': 'Fase', 'total_points': 'Pontos Totais'}
                    )
                    st.plotly_chart(fig, use_container_width=True)
        
        else:
            # Show top recommended alliances based on challenge and phase complementarity
            alliances = construir_alianca_otima(team_rankings, challenge_rankings, df, alliance_size)
            
            st.subheader(f"Melhores Alianças Complementares (Tamanho: {alliance_size})")
            
            # Display each alliance with its coverage
            for i, alliance in enumerate(alliances[:3]):
                st.markdown(f"### Aliança {i+1} - {int(alliance['total_points'])} pontos")
                
                # Show teams in horizontal columns
                team_cols = st.columns(len(alliance['teams']))
                for j, team in enumerate(alliance['teams']):
                    with team_cols[j]:
                        team_data = team_rankings[team_rankings['team'] == team].iloc[0]
                        
                        # Get team's best challenge
                        best_challenge = challenge_rankings[
                            challenge_rankings['team'] == team
                        ].sort_values('total_points', ascending=False)
                        
                        if not best_challenge.empty:
                            best_challenge_name = best_challenge.iloc[0]['challenge_name']
                        else:
                            best_challenge_name = "N/A"
                        
                        # Get team's best phase
                        best_phase = df.loc[df['team'] == team].groupby(['challenge_name', 'phase_name']).agg({
                            'total_points': 'sum'
                        }).reset_index().sort_values('total_points', ascending=False).iloc[0]
                        
                        if not best_phase.empty:
                            best_phase_info = f"{best_phase['challenge_name']} ({best_phase['phase_name']})"
                        else:
                            best_phase_info = "N/A"
                        
                        st.metric(
                            f"Equipe {j+1}", 
                            team, 
                            f"Rank: {int(team_data['rank'])}"
                        )
                        
                        st.markdown(f"""
                        **Melhor Desafio:** {best_challenge_name}  
                        **Melhor Fase:** {best_phase_info}
                        """)
                
                # Show alliance challenge coverage
                if 'challenge_coverage' in alliance and not alliance['challenge_coverage'].empty:
                    coverage_data = alliance['challenge_coverage'].sort_values('total_points')
                    
                    fig = px.bar(
                        coverage_data,
                        y='challenge_name',
                        x='total_points',
                        orientation='h',
                        title="Cobertura de Desafios",
                        labels={'challenge_name': 'Desafio', 'total_points': 'Pontos Totais'}
                    )
                    st.plotly_chart(fig, use_container_width=True)
                
                # Show phase coverage for a representative challenge
                if 'phase_coverage' in alliance and not alliance['phase_coverage'].empty:
                    # Get a representative challenge (lowest performing)
                    if 'challenge_coverage' in alliance and not alliance['challenge_coverage'].empty:
                        rep_challenge = alliance['challenge_coverage'].sort_values('total_points').iloc[0]['challenge_name']
                        
                        # Filter phase coverage for this challenge
                        phase_data = alliance['phase_coverage'][
                            alliance['phase_coverage']['challenge_name'] == rep_challenge
                        ]
                        
                        if not phase_data.empty:
                            fig = px.bar(
                                phase_data,
                                x='phase_name',
                                y='total_points',
                                title=f"Cobertura de Fases no Desafio {rep_challenge}",
                                labels={'phase_name': 'Fase', 'total_points': 'Pontos Totais'}
                            )
                            st.plotly_chart(fig, use_container_width=True)
                
                st.markdown("---")  # Add a separator between alliances

if __name__ == "__main__":
    main()