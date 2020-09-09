.. Summit documentation master file, created by
   sphinx-quickstart on Fri Aug 28 21:25:06 2020.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Welcome to Summit's documentation!
==================================

.. image:: _static/banner_4.png
  :alt: Summit banner

Summit is a set of tools for optimising chemical processes. We’ve started by targeting reactions.

What is Summit?
##################
Currently, reaction optimisation in the fine chemicals industry is done by intuition or design of experiments,  Both scale poorly with the complexity of the problem. 

Summit uses recent advances in machine learning to make the process of reaction optimisation faster. Essentially, it applies algorithms that learn which conditions (e.g., temperature, stoichiometry, etc.) are important to maximising one or more objectives (e.g., yield, enantiomeric excess). This is achieved through an iterative cycle.

Summit has two key features:

* **Strategies**: Optimisation algorithms designed to find the best conditions with the least number of iterations. Summit has eight strategies implemented.
* **Benchmarks**: Simulations of chemical reactions that can be used to test strategies. We have both mechanistic and data-driven benchmarks.

To get started, follow our tutorial_.

.. _tutorial : tutorial.ipynb
.. toctree::
   :maxdepth: 2
   :caption: Contents:
   
   installation
   tutorial
   new_benchmarks


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
