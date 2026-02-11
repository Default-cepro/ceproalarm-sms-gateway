import pandas as pd

def read_test(path: str) -> pd.DataFrame:
    return pd.read_excel(path,header=None,sheet_name=0)

path = r"C:\Users\agarellano\Desktop\proyecto\ceproalarm-sms-gateway\tests\exctest.xlsx"
print(read_test(path),"\n")

#print (read_test(path).columns)

for idx,row in read_test(path).iterrows():
    print(idx,row[1])
print("\n")

for row in read_test(path).itertuples():
    print(row)
print("\n")

for row in read_test(path).values.tolist():
    print(row)
print("\n")

print(read_test(path))
    
#for i in read_test(path=r"C:\Users\agarellano\Desktop\proyecto\ceproalarm-sms-gateway\tests\exctest.xlsx"):
#    print(i)