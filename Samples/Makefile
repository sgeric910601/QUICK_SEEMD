all: build

build: main.o util.o
	$(CC) -o app $^ $(LDFLAGS)

.PHONY: clean
clean:
	rm -f *.o app # 清除
