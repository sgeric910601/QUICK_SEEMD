package main

import (
	"fmt"
	"net/http"
)

type Server struct {
	Addr string
	hits int
}

func (s *Server) handle(w http.ResponseWriter, r *http.Request) {
	s.hits++
	fmt.Fprintf(w, "hello #%d from %s\n", s.hits, r.URL.Path) // 回應
}

func main() {
	s := &Server{Addr: ":8080"}
	http.HandleFunc("/", s.handle)
	_ = http.ListenAndServe(s.Addr, nil)
}
