FROM orchardup/python:2.7
ADD . /code
WORKDIR /code
RUN pip install redis flask docker-py ipython
RUN apt-get update
RUN apt-get -y install libssl-dev libffi-dev git
RUN git clone https://github.com/jplana/python-etcd.git
RUN cd /code/python-etcd && python ./setup.py install
CMD ["python","/code/soa.py"]
