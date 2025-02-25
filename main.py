from flask import Flask, send_file, jsonify, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from threading import Lock
import requests
import uuid

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

# Thread-safe counter for active users
thread_lock = Lock()
active_users = 0

# Хранилище активных игр и игроков
games = {}
waiting_players = {}
player_rooms = {}  # Хранит соответствие игрок -> комната

@app.route('/')
def serve_page():
    return send_file('nikitaand.html')

@app.route('/get_ip_info')
def get_ip_info():
    # Получаем IP клиента
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    
    try:
        # Используем ipapi.co для получения информации об IP
        response = requests.get(f'https://ipapi.co/{client_ip}/json/')
        data = response.json()
        
        return jsonify({
            'ip': data.get('ip', 'Unknown'),
            'city': data.get('city', 'Unknown'),
            'region': data.get('region', 'Unknown'),
            'country': data.get('country_name', 'Unknown'),
            'org': data.get('org', 'Unknown'),
            'postal': data.get('postal', 'Unknown'),
            'timezone': data.get('timezone', 'Unknown'),
            'latitude': data.get('latitude', 'Unknown'),
            'longitude': data.get('longitude', 'Unknown')
        })
    except:
        return jsonify({'error': 'Failed to get IP info'})

@socketio.on('connect')
def handle_connect():
    global active_users
    with thread_lock:
        active_users += 1
        socketio.emit('user_count', {'count': active_users})

@socketio.on('disconnect')
def handle_disconnect():
    global active_users
    player_id = request.sid
    
    with thread_lock:
        active_users = max(0, active_users - 1)
        socketio.emit('user_count', {'count': active_users})
    
    # Очищаем игры при отключении
    if player_id in waiting_players:
        del waiting_players[player_id]
    
    # Если игрок был в игре, уведомляем противника
    if player_id in player_rooms:
        game_id = player_rooms[player_id]
        if game_id in games:
            game = games[game_id]
            other_player = game['player1'] if player_id == game['player2'] else game['player2']
            emit('game_ended', {
                'message': 'Противник отключился',
                'type': 'disconnect'
            }, room=other_player)
            
            # Очищаем данные игры
            if other_player in player_rooms:
                del player_rooms[other_player]
            if player_id in player_rooms:
                del player_rooms[player_id]
            del games[game_id]

@socketio.on('trigger_fire')
def handle_trigger_fire():
    socketio.emit('trigger_fire')

@socketio.on('join_game')
def handle_join_game():
    player_id = request.sid
    
    # Если игрок уже в игре, отключаем его от предыдущей
    if player_id in player_rooms:
        old_game_id = player_rooms[player_id]
        if old_game_id in games:
            leave_room(old_game_id)
            del player_rooms[player_id]
    
    # Если есть ожидающие игроки, создаем игру
    if waiting_players:
        opponent_id, _ = waiting_players.popitem()
        if opponent_id != player_id:  # Упрощаем проверку
            game_id = str(uuid.uuid4())
            games[game_id] = {
                'player1': opponent_id,
                'player2': player_id,
                'board': ['' for _ in range(9)],
                'current_turn': opponent_id
            }
            
            # Присоединяем обоих игроков к комнате
            join_room(game_id, sid=opponent_id)
            join_room(game_id, sid=player_id)
            
            player_rooms[opponent_id] = game_id
            player_rooms[player_id] = game_id
            
            # Отправляем начальное состояние обоим игрокам
            emit('game_started', {
                'game_id': game_id,
                'symbol': 'X',
                'your_turn': True,
                'board': games[game_id]['board']
            }, room=opponent_id)
            
            emit('game_started', {
                'game_id': game_id,
                'symbol': 'O',
                'your_turn': False,
                'board': games[game_id]['board']
            }, room=player_id)
        else:
            # Если что-то пошло не так, добавляем игрока в ожидание
            waiting_players[player_id] = True
            emit('waiting_for_opponent')
    else:
        # Добавляем игрока в список ожидания
        waiting_players[player_id] = True
        emit('waiting_for_opponent')

@socketio.on('make_move')
def handle_make_move(data):
    game_id = data['game_id']
    position = data['position']
    player_id = request.sid
    
    if game_id in games:
        game = games[game_id]
        if player_id == game['current_turn'] and game['board'][position] == '':
            # Определяем символ игрока
            symbol = 'X' if player_id == game['player1'] else 'O'
            game['board'][position] = symbol
            
            # Проверяем победу
            winning_combo = check_winner(game['board'], symbol)
            if winning_combo:
                emit('game_over', {
                    'winner': player_id,
                    'combo': winning_combo,
                    'board': game['board']
                }, room=game_id)
                cleanup_game(game_id)
            # Проверяем ничью
            elif '' not in game['board']:
                emit('game_over', {
                    'winner': None,
                    'board': game['board']
                }, room=game_id)
                cleanup_game(game_id)
            else:
                # Передаем ход другому игроку
                game['current_turn'] = game['player2'] if player_id == game['player1'] else game['player1']
                emit('move_made', {
                    'position': position,
                    'symbol': symbol,
                    'next_turn': game['current_turn'],
                    'board': game['board']
                }, room=game_id)

def check_winner(board, symbol):
    # Проверка строк, столбцов и диагоналей
    winning_combinations = [
        [0, 1, 2], [3, 4, 5], [6, 7, 8],  # Строки
        [0, 3, 6], [1, 4, 7], [2, 5, 8],  # Столбцы
        [0, 4, 8], [2, 4, 6]  # Диагонали
    ]
    for combo in winning_combinations:
        if all(board[i] == symbol for i in combo):
            return combo
    return None

def cleanup_game(game_id):
    if game_id in games:
        game = games[game_id]
        # Очищаем связи игроков с игрой
        if game['player1'] in player_rooms:
            del player_rooms[game['player1']]
        if game['player2'] in player_rooms:
            del player_rooms[game['player2']]
        # Удаляем игру
        del games[game_id]

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=7777, debug=True)
