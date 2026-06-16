from datetime import date, timedelta
def calcula_intervalo(fecha_actual):
    # Fecha actual (puedes usar date.today() para la fecha del sistema)
    hoy = date(2026, 3, 30)  # Ejemplo: 30 de marzo de 2026

    # Calcular el lunes de la semana actual
    lunes_actual = hoy - timedelta(days=hoy.weekday())  # weekday(): lunes=0, domingo=6

    # Calcular el lunes y domingo de la semana anterior
    lunes_semana_anterior = lunes_actual - timedelta(days=7)
    domingo_semana_anterior = lunes_semana_anterior + timedelta(days=6)

    # Mostrar resultados
    #print("Inicio de semana vencida:", inicio_semana_vencida.strftime("%d/%m/%Y"))
    #print("Fin de semana vencida:", fin_semana_vencida.strftime("%d/%m/%Y"))
    return lunes_semana_anterior, domingo_semana_anterior