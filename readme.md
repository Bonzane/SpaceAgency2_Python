# Space Agency 2 - Python Server

*Command to run the server:*
./run.sh


## Table of Contents
- [Introduction](#introduction)
- [Set-Up](#setup)

## Introduction
This is the public project for creating your own customizable game-servers for Space Agency 2.
Game servers are community-hosted and designed to allow friends to play together. These function
similarly to the official servers, but will not be listed publicly. Thank you for cloning this project and
for joining the Space Agency 2 hosts & developers community! 

There are a lot of parts to a Space Agency server, so before getting started it's helpful to have a basic understanding of what they are. 

A "Game" within these servers refers to one world-state. For example, I have 3 satellites orbiting Earth
and my friend Caiden has 4. In another game on the same server, we might be in the same agency and have 4 satellites, 3 telescopes, and a moon lander. When launcing a server, you'll need to specify which "Game"
will be running, so that the server knows where to find the files that describe all of the world information.

The universe in Space Agency 2 is made of Chunks that contain objects. Physics run within one Chunk at a time, and this can use a lot of parallel compute, so a multi-core CPU is highly recommended. Any chunk a player is in will run real-time physics. Other chunks will calculate the positions of objects based on their former positions, and the amount of time that has passed since that chunk was last running real-time physics. The "biggest chunk" is the Intergalactic Chunk, which contains a map of all the galaxies. When a player gets close to one of these galaxies, they enter that galaxy's "Interstellar Chunk". When a player gets close to a star in that interstellar chunk, they enter that star's Solar Chunk, which might contain planets, strange objects, asteroids, etc. This gives the illusion that the Map is actually the size of the observable universe, since you can fly across it in a straight line, but in reality the mostly-empty intergalatic and interstellar space is stored in smaller files. However, as players explore more and more of your game's universe, more data has to be stored on your Disk, so if hosting large games be sure to allocate a few gigabytes on disk space for your Game. 

While it's fun to be able to host your own games with your own settings, there is also risks associated with community-hosted servers. For this reason, community hosted servers will not appear on the in-game server browser by default. Hyder LLC may reward server hosts with a "Community Trusted" status that allows community servers to appear in the alternate community browser after acknowledging the risks of community content, but otherwise your clients must connect directly. To do this you'll need to give them an IPV4 or domain to connect to. It might be a good idea to set up a website where clients can access your server from.

## Set-Up
The parameters for running the server are defined in config.txt. Most basic game settings 
can be changed by editing this file alone. That way, if you want to host servers on behalf of someone else, 
they can provide you with the configuration file which is easily interchangable with the rest of the server.



