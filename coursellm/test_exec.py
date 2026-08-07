import sqlmodel
print(sqlmodel.Session.exec.__code__.co_varnames)
