from bs4 import BeautifulSoup
import requests

from updatedb import store_ranking

html_request=requests.get('https://www.ufc.com/rankings').text
soup = BeautifulSoup(html_request,'lxml')
pound_for_pound=[]
flyweight=[]
bantamweight=[]
featherweight=[]
lightweight=[]
welterweight=[]
middleweight=[]
lightheavyweight=[]
heavyweight=[]
womenpfp=[]
womenstrawweight=[]
womenflyweight=[]
womenbantamweight=[]

rankings=soup.find_all('div',class_='view-grouping')



pfptable = rankings[0].find('tbody')
flyweighttable = rankings[1].find('tbody')
bantamweighttable = rankings[2].find('tbody')
featherweighttable = rankings[3].find('tbody')
lightweighttable = rankings[4].find('tbody')
welterweighttable = rankings[5].find('tbody')
middleweighttable = rankings[6].find('tbody')
lightheavyweighttable = rankings[7].find('tbody')
heavyweighttable = rankings[8].find('tbody')
womenpfptable = rankings[9].find('tbody')
womenstrawweighttable = rankings[10].find('tbody')
womenflyweighttable = rankings[11].find('tbody')
womenbantamweighttable = rankings[12].find('tbody')

flyweightchamp = rankings[1].find('a').text
flyweight.append(flyweightchamp)
bantamweightchamp = rankings[2].find('a').text
bantamweight.append(bantamweightchamp)
featherweightchamp = rankings[3].find('a').text
featherweight.append(featherweightchamp)
lightweightchamp = rankings[4].find('a').text
lightweight.append(lightweightchamp)
welterweightchamp = rankings[5].find('a').text
welterweight.append(welterweightchamp)
middleweightchamp = rankings[6].find('a').text
middleweight.append(middleweightchamp)
lightheavyweightchamp = rankings[7].find('a').text
lightheavyweight.append(lightheavyweightchamp)
heavyweightchamp = rankings[8].find('a').text
heavyweight.append(heavyweightchamp)
womenstrawweightchamp = rankings[10].find('a').text
womenstrawweight.append(womenstrawweightchamp)
womenflyweightchamp = rankings[11].find('a').text
womenflyweight.append(womenflyweightchamp)
womenbantamweightchamp = rankings[12].find('a').text
womenbantamweight.append(womenbantamweightchamp)


rowspfp = pfptable.find_all('tr')
for fighter in rowspfp:
    name = fighter.find('a').text
    pound_for_pound.append(name)

rowsfw = flyweighttable.find_all('tr')
for fighter in rowsfw:
    name = fighter.find('a').text
    flyweight.append(name)

rowsbw = bantamweighttable.find_all('tr')
for fighter in rowsbw:
    name = fighter.find('a').text
    bantamweight.append(name)

rowsfw2 = featherweighttable.find_all('tr')
for fighter in rowsfw2:
    name = fighter.find('a').text
    featherweight.append(name)

rowslw = lightweighttable.find_all('tr')
for fighter in rowslw:
    name = fighter.find('a').text
    lightweight.append(name)

rowsww = welterweighttable.find_all('tr')
for fighter in rowsww:
    name = fighter.find('a').text
    welterweight.append(name)

rowsmw = middleweighttable.find_all('tr')
for fighter in rowsmw:
    name = fighter.find('a').text
    middleweight.append(name)

rowslhw = lightheavyweighttable.find_all('tr')
for fighter in rowslhw:
    name = fighter.find('a').text
    lightheavyweight.append(name)

rowshw = heavyweighttable.find_all('tr')
for fighter in rowshw:
    name = fighter.find('a').text
    heavyweight.append(name)

rowswpfp = womenpfptable.find_all('tr')
for fighter in rowswpfp:
    name = fighter.find('a').text
    womenpfp.append(name)

rowswsw = womenstrawweighttable.find_all('tr')
for fighter in rowswsw:
    name = fighter.find('a').text
    womenstrawweight.append(name)

rowswfw = womenflyweighttable.find_all('tr')
for fighter in rowswfw:
    name = fighter.find('a').text
    womenflyweight.append(name)

rowswbw = womenbantamweighttable.find_all('tr')
for fighter in rowswbw:
    name = fighter.find('a').text
    womenbantamweight.append(name)

print("Pound for Pound:", pound_for_pound)
print("Flyweight:", flyweight)
print("Bantamweight:", bantamweight)
print("Featherweight:", featherweight)
print("Lightweight:", lightweight)
print("Welterweight:", welterweight)
print("Middleweight:", middleweight)
print("Light Heavyweight:", lightheavyweight)
print("Heavyweight:", heavyweight)
print("Women Pound for Pound:", womenpfp)
print("Women Strawweight:", womenstrawweight)
print("Women Flyweight:", womenflyweight)
print("Women Bantamweight:", womenbantamweight)

store_ranking("Pound for Pound", None, pound_for_pound, "men")
store_ranking("Flyweight", flyweightchamp, flyweight, "men")
store_ranking("Bantamweight", bantamweightchamp, bantamweight, "men")
store_ranking("Featherweight", featherweightchamp, featherweight, "men")
store_ranking("Lightweight", lightweightchamp, lightweight, "men")
store_ranking("Welterweight", welterweightchamp, welterweight, "men")
store_ranking("Middleweight", middleweightchamp, middleweight, "men")
store_ranking("Light Heavyweight", lightheavyweightchamp, lightheavyweight, "men")
store_ranking("Heavyweight", heavyweightchamp, heavyweight, "men")

store_ranking("Pound for Pound", None, womenpfp, "women")
store_ranking("Strawweight", womenstrawweightchamp, womenstrawweight, "women")
store_ranking("Flyweight", womenflyweightchamp, womenflyweight, "women")
store_ranking("Bantamweight", womenbantamweightchamp, womenbantamweight, "women")

print("✅ Rankings store_ranking successfully")
