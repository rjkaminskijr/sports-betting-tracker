def infer_sport(text: str) -> str | None:
    t = text.upper()
    nfl = ['CARDINALS','FALCONS','RAVENS','BILLS','PANTHERS','BEARS','BENGALS','BROWNS','COWBOYS','BRONCOS','LIONS','PACKERS','TEXANS','COLTS','JAGUARS','CHIEFS','RAIDERS','CHARGERS','RAMS','DOLPHINS','VIKINGS','PATRIOTS','SAINTS','GIANTS','JETS','EAGLES','STEELERS','49ERS','SEAHAWKS','BUCCANEERS','TITANS','COMMANDERS','TOUCHDOWN','ANYTIME TD SCORER']
    nba = ['CAVALIERS','KNICKS','BRUNSON','MITCHELL','1ST QUARTER']
    golf = ['HOVLAND','MCILROY','RAHM','SCHEFFLER','GOTTERUP','OPEN CHAMPIONSHIP','INCLUDING TIES','OUTRIGHT WINNER']
    soccer = ['GOALSCORER','CZECH REPUBLIC','SOUTH KOREA','BRAZIL','NORWAY']
    baseball = ['HOME RUN DERBY','TOTAL HRS']
    for sport, words in [('NFL',nfl),('NBA',nba),('GOLF',golf),('SOCCER',soccer),('BASEBALL',baseball)]:
        if any(w in t for w in words): return sport
    return None
