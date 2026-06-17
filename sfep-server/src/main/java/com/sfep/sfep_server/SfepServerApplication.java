package com.sfep.sfep_server;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class SfepServerApplication {

	public static void main(String[] args) {
		SpringApplication application = new SpringApplication(SfepServerApplication.class);
		application.setHeadless(false);
		application.run(args);
	}

}
