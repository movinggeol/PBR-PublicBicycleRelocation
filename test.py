import matplotlib.pyplot as plt
from matplotlib import font_manager, rc
import pandas as pd



def month_graph(df : pd.DataFrame):
    print(df)

    plt.figure(figsize=(10,5))
    plt.plot(df['month'], df['count'], marker='o')
    
    plt.title('월별 대여량')
    plt.xlabel('월')
    plt.ylabel('대여량')
    plt.grid(True)



    plt.show()



if __name__ == '__main__':

    font_path = 'C:/Windows/Fonts/malgun.ttf'
    font_name = font_manager.FontProperties(fname=font_path).get_name()

    rc('font', family=font_name)

    plt.rcParams['axes.unicode_minus'] = False

    rent = {'01월' : 1000, '02월' : 1200, '03월' : 1300, 
            '04월' : 1600, '05월' : 2000, '06월' : 2200, 
            '07월' : 2250, '08월' : 2600, '09월' : 3500, 
            '10월' : 3200, '11월' : 1400, '12월' : 1200}
    #print(rent)

    df = pd.DataFrame({'month' : rent.keys(), 'count' : rent.values()})

    month_graph(df)